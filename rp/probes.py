"""Probes: the small set of ways a rule can extract a value.

A rule does not contain code. It names a probe and gives it arguments, which is
what makes rules cheap enough to have thousands of. Everything a sensor could
want to know has to reduce to one of these, and keeping the set small is the
point -- a probe per sensor would just be functions again with extra ceremony.

Probes are pure lookups. They decide nothing, weigh nothing, and know nothing
about channels; a probe that returned a judgement would put the interpretation
back inside the extraction where nobody can see it.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import time
from functools import lru_cache

# --- shell ------------------------------------------------------------------


@lru_cache(maxsize=512)
def run(cmd: tuple[str, ...], cwd: str | None = None, timeout: int = 60) -> str | None:
    """A command's stdout, or **None if the command failed**.

    None rather than empty string, and that distinction is the point. A failed
    command and a command that found nothing are opposite facts, and returning
    "" for both is how this system reports a broken probe as a clean result. It
    has happened three times: a malformed git format string silenced an entire
    probe kind, and a missing `-e` made every content rule in the fleet go
    quiet while nothing anywhere said so.

    Cached per pass: several rules ask the same question, and a rule set is
    supposed to be written without its author tracking who else already asked.
    """
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True,
                           timeout=timeout, shell=False, cwd=cwd)
        return p.stdout if p.returncode == 0 else None
    except Exception:
        return None


def text(out: str | None) -> str:
    """For callers that genuinely cannot act on the difference."""
    return out or ""


def git(root: str, *args: str) -> str:
    return text(run(("git", "-C", root, *args)))


# --- filesystem -------------------------------------------------------------


@lru_cache(maxsize=256)
def listing(root: str) -> tuple[str, ...]:
    """Every file under a root, relative and slash-normalised.

    Prefers git's index when there is one -- it is faster and already excludes
    build output -- and falls back to a walk so the probe works on directories
    that are not repositories. That fallback is what makes the whole system
    work outside a version-controlled tree.
    """
    tracked = git(root, "ls-files")
    if tracked.strip():
        return tuple(f.strip() for f in tracked.splitlines() if f.strip())
    out: list[str] = []
    skip = {".git", "node_modules", "target", "build", "__pycache__", ".venv",
            ".dart_tool", "dist", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            out.append(rel.replace("\\", "/"))
        if len(out) > 60_000:            # a root this big is not a subject
            break
    return tuple(out)


def matches(root: str, pattern: str) -> list[str]:
    """Files matching a glob. `**/` means anywhere."""
    files = listing(root)
    if pattern.startswith("**/"):
        tail = pattern[3:]
        return [f for f in files if fnmatch.fnmatch(os.path.basename(f), tail)]
    return [f for f in files if fnmatch.fnmatch(f, pattern)]


def _has_glob(pattern: str) -> bool:
    return any(c in pattern for c in "*?[")


def exists(root: str, pattern: str) -> float:
    """Presence. Checks the filesystem, not git's index.

    `listing()` prefers `git ls-files` because it is fast and pre-filtered, but
    the index cannot see directories or untracked files -- and a lot of the
    most useful evidence is exactly that: `.github/workflows` is a directory,
    `ester_analysis.md` and `.pytest_cache` are generated and gitignored.
    Asking the index made `no-ci` and `unreviewed` fire on all eighteen
    projects while the recognizers, which do stat, correctly saw otherwise.
    """
    if not _has_glob(pattern) and os.path.exists(os.path.join(root, pattern)):
        return 1.0
    return 1.0 if matches(root, pattern) else 0.0


def absent(root: str, pattern: str) -> float:
    return 0.0 if exists(root, pattern) else 1.0


def count(root: str, pattern: str) -> float:
    n = len(matches(root, pattern))
    if n:
        return float(n)
    # A directory glob like `docs/*` matches nothing in git's index when the
    # contents are untracked, so fall back to the disk. Only for patterns that
    # actually name a directory: `**/*.jks` has an empty prefix, and counting
    # the project root for it made a keystore-detector fire on all eighteen
    # projects.
    head, sep, _ = pattern.partition("*")
    head = head.rstrip("/")
    if not sep or not head or head.endswith("*"):
        return 0.0
    full = os.path.join(root, head)
    if os.path.isdir(full):
        try:
            return float(len([e for e in os.scandir(full) if e.is_file()]))
        except OSError:
            return 0.0
    return 0.0


def bytes_over(root: str, pattern: str, limit: int) -> float:
    """How many matching files exceed a size."""
    n = 0
    for f in matches(root, pattern):
        try:
            if os.path.getsize(os.path.join(root, f)) > limit:
                n += 1
        except OSError:
            pass
    return float(n)


# A line carrying this marker is invisible to every text and grammar probe.
#
# Not a convenience: it is structural. Every rule that detects a dangerous
# pattern needs test fixtures containing that pattern, and rule definitions
# themselves contain the patterns they search for — so a detector library
# reliably detects itself. This system's own secrets rule fired on its own unit
# test, which held a fake key written to prove the rule worked.
#
# One marker rather than a list of excluded paths, because the exclusion
# belongs at the site that knows why, and a path list silently stops matching
# when files move.
ALLOW_MARKER = "rp:allow"


def content(root: str, pattern: str, regex: str) -> float:
    """Lines matching a regex across matching files.

    Reads through git's own grep when possible, which is index-backed and does
    not walk the tree. Lines carrying ALLOW_MARKER are excluded there too, via
    grep's own --and --not, so the filter costs nothing.
    """
    if git(root, "rev-parse", "HEAD").strip():
        # `-e` on BOTH patterns. Without it git parses `--and` as a revision and
        # fails outright, which silenced every content rule in the fleet while
        # each one reported a clean zero.
        out = run(("git", "-C", root, "grep", "-I", "-c", "-E",
                   "-e", regex, "--and", "--not", "-e", ALLOW_MARKER,
                   "--", pattern))
        if out is None:
            # git grep exits non-zero for "no matches" as well as for a broken
            # invocation, so fall through to reading the files rather than
            # guessing which happened.
            pass
        else:
            total = 0.0
            for line in out.splitlines():
                _, _, n = line.rpartition(":")
                try:
                    total += int(n)
                except ValueError:
                    pass
            return total
    rx = re.compile(regex)
    total = 0.0
    for f in matches(root, pattern)[:400]:
        try:
            with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                total += sum(1 for line in fh
                             if rx.search(line) and ALLOW_MARKER not in line)
        except OSError:
            pass
    return total


def age_days(root: str, pattern: str) -> float:
    """Days since the newest matching file changed. Infinite when absent."""
    best = 0.0
    for f in matches(root, pattern):
        try:
            best = max(best, os.path.getmtime(os.path.join(root, f)))
        except OSError:
            pass
    return (time.time() - best) / 86400.0 if best else float("inf")


def json_key(root: str, path: str, key: str) -> float:
    """A numeric value out of a JSON file, dotted path."""
    try:
        with open(os.path.join(root, path), encoding="utf-8") as fh:
            node = json.load(fh)
    except (OSError, ValueError):
        return 0.0
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return 0.0
    if isinstance(node, (list, dict)):
        return float(len(node))
    try:
        return float(node)
    except (TypeError, ValueError):
        return 0.0


# --- grammar ----------------------------------------------------------------
#
# Every probe here can return UNKNOWN, and that is the point of the kind. A
# parser that chokes on one syntax version returns nothing, and nothing is
# indistinguishable from clean — so "could not read" is a third answer, never a
# zero. Rules treat UNKNOWN as no finding, which is honest; what they must not
# do is treat it as a pass.

UNKNOWN = float("nan")


def longest_function(root: str, pattern: str, threshold: int) -> float:
    """Functions longer than `threshold` lines. Regex cannot see extent."""
    from . import grammar

    n = 0
    seen = False
    for rel in matches(root, pattern)[:300]:
        full = os.path.join(root, rel)
        kind = grammar.parse(full)
        if not kind:
            continue
        try:
            fns = (grammar.py_functions(full) if kind == "python"
                   else grammar.braced_functions(full))
        except grammar.ParseFailure:
            continue
        seen = True
        n += sum(1 for f in fns if f[2] > threshold)
    return float(n) if seen else UNKNOWN


def deep_nesting(root: str, pattern: str, threshold: int) -> float:
    """Files nested deeper than `threshold`. Complexity you feel while reading."""
    from . import grammar

    n = 0
    seen = False
    for rel in matches(root, pattern)[:300]:
        full = os.path.join(root, rel)
        if not grammar.parse(full):
            continue
        try:
            depth = (grammar.py_max_depth(full) if grammar.parse(full) == "python"
                     else grammar.max_indent_depth(full))
        except grammar.ParseFailure:
            continue
        seen = True
        n += depth > threshold
    return float(n) if seen else UNKNOWN


def py_smell(root: str, which: str) -> float:
    """One named Python smell that needs a parser to see.

    A mutable default argument is invisible to regex: it requires knowing the
    default is a literal container AND that it belongs to a parameter.
    """
    from . import grammar

    total = 0.0
    seen = False
    for rel in matches(root, "*.py")[:400]:
        try:
            counts = grammar.py_smells(os.path.join(root, rel))
        except grammar.ParseFailure:
            continue
        seen = True
        total += counts.get(which, 0)
    return total if seen else UNKNOWN


def wide_signatures(root: str, pattern: str, threshold: int) -> float:
    """Functions taking more arguments than a reader can hold."""
    from . import grammar

    n = 0
    seen = False
    for rel in matches(root, pattern)[:300]:
        full = os.path.join(root, rel)
        if grammar.parse(full) != "python":
            continue
        try:
            fns = grammar.py_functions(full)
        except grammar.ParseFailure:
            continue
        seen = True
        n += sum(1 for f in fns if f[3] > threshold)
    return float(n) if seen else UNKNOWN


def unused_privates(root: str, pattern: str = "*.py") -> float:
    from . import grammar

    total = 0.0
    seen = False
    for rel in matches(root, pattern)[:400]:
        try:
            total += grammar.py_unused_privates(os.path.join(root, rel))
        except grammar.ParseFailure:
            continue
        seen = True
    return total if seen else UNKNOWN


def untyped_share(root: str, pattern: str = "*.py") -> float:
    """Share of Python parameters with no annotation, as a percentage."""
    from . import grammar

    covered: list[float] = []
    for rel in matches(root, pattern)[:400]:
        try:
            covered.append(grammar.py_annotation_coverage(os.path.join(root, rel)))
        except grammar.ParseFailure:
            continue
    if not covered:
        return UNKNOWN
    return (1.0 - sum(covered) / len(covered)) * 100.0


# --- history ----------------------------------------------------------------
#
# Every probe here takes its window as an argument and the rule states it, so
# the number is comparable to something. "Ten commits" over a week and over a
# year are different facts.


def _history(name: str):
    def probe(root: str, *args) -> float:
        from . import history
        try:
            value = float(getattr(history, name)(root, *args))
        except Exception:
            # UNKNOWN, not zero. A probe that cannot answer must not be
            # indistinguishable from one that answered "nothing here" — the
            # first version returned 0.0 and hid a bug that had silenced every
            # probe in this kind across the whole fleet.
            return UNKNOWN
        return UNKNOWN if value == float("inf") else value
    probe.__name__ = f"history_{name}"
    return probe


# --- expectation ------------------------------------------------------------


def version_disagreement(root: str, _unused: int = 0) -> float:
    """1 when the manifest version and the newest release tag disagree."""
    from . import expectation
    try:
        return 1.0 if expectation.version_drift(root) else 0.0
    except Exception:
        return UNKNOWN


def undeclared_deps(root: str, _unused: int = 0) -> float:
    """Third-party imports the project never declares."""
    from . import expectation
    try:
        return float(len(expectation.undeclared_imports(root)))
    except Exception:
        return UNKNOWN


# --- identity ---------------------------------------------------------------


def duplicate_files(root: str, _unused: int = 0) -> float:
    """Byte-identical source files inside one project."""
    from . import identity
    try:
        return float(identity.duplicates_within(
            identity.source_digests(root, list(listing(root)))))
    except Exception:
        return UNKNOWN


def stale_binaries(root: str, _unused: int = 0) -> float:
    """Committed build output older than the source it came from."""
    from . import identity
    try:
        return float(identity.stale_artifacts(root, list(listing(root))))
    except Exception:
        return UNKNOWN


# --- verdict ----------------------------------------------------------------


def failing_test_share(root: str, _unused: int = 0) -> float:
    """Failing test files as a percentage of collected tests.

    UNKNOWN rather than zero when the cache has expired: a stale pass is not a
    pass, and this kind's whole discipline is refusing to answer from evidence
    that no longer describes the code.
    """
    from . import verdict
    try:
        # A cache proves pytest ran here once, not that pytest is the runner.
        # Believing a stray one reported a project whose suite passes as having
        # five failing files.
        if not verdict.uses_pytest(root):
            return UNKNOWN
        if verdict.test_cache_age_days(root) > 3.0:
            return UNKNOWN
        failing, total = verdict.test_totals(root)
        if not total:
            return UNKNOWN
        return failing / total * 100.0
    except Exception:
        return UNKNOWN


def log_errors(root: str, window_days: int = 7) -> float:
    """Error lines written to the project's own logs inside a window."""
    from . import verdict
    try:
        return float(verdict.recent_log_errors(root, float(window_days))[0])
    except Exception:
        return UNKNOWN


# --- execution (read as a verdict) and remote (read as a snapshot) ----------


def run_failed(root: str, label: str) -> float:
    """1 when the last recorded run of `label` failed, at the current commit.

    UNKNOWN when the receipt is stale or absent. A wrapper that stops being
    used writes nothing, and its last receipt goes on saying whatever it said —
    absence of a recorded failure is not evidence of a success.
    """
    from . import verdict
    try:
        state, _ = verdict.run_state(root, label)
    except Exception:
        return UNKNOWN
    if state == "fail":
        return 1.0
    if state == "pass":
        return 0.0
    return UNKNOWN


def run_unverified(root: str, label: str) -> float:
    """1 when nothing has recorded a `label` run at the current commit."""
    from . import verdict
    try:
        state, _ = verdict.run_state(root, label)
    except Exception:
        return UNKNOWN
    return 1.0 if state in ("stale", "never") else 0.0


def vulnerable_deps(root: str, _unused: int = 0) -> float:
    """Declared dependencies with known advisories.

    UNKNOWN unless a usable snapshot exists. "Could not check" is not a pass,
    and for a security-shaped signal that distinction is the only thing that
    makes it safe to publish at all.
    """
    from . import remote
    try:
        if not remote.usable():
            return UNKNOWN
        return float(remote.advisories_for(root)[0])
    except Exception:
        return UNKNOWN


# --- machine ----------------------------------------------------------------


@lru_cache(maxsize=1)
def processes() -> tuple[str, ...]:
    """Running image names, lowercased."""
    out = text(run(("tasklist", "/fo", "csv", "/nh")))
    names = []
    for line in out.splitlines():
        if line.startswith('"'):
            names.append(line.split('","')[0].strip('"').lower())
    return tuple(names)


def process_running(_root: str, name: str) -> float:
    return 1.0 if name.lower() in processes() else 0.0


@lru_cache(maxsize=1)
def listening_ports() -> tuple[int, ...]:
    out = text(run(("netstat", "-ano", "-p", "TCP")))
    ports = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3] == "LISTENING":
            _, _, port = parts[1].rpartition(":")
            try:
                ports.add(int(port))
            except ValueError:
                pass
    return tuple(sorted(ports))


def port_open(_root: str, port: int) -> float:
    return 1.0 if int(port) in listening_ports() else 0.0


def disk_free_pct(_root: str, drive: str) -> float:
    try:
        usage = shutil.disk_usage(f"{drive}:\\")
    except OSError:
        return 100.0
    return usage.free / usage.total * 100.0


@lru_cache(maxsize=1)
def gpu() -> tuple[float, float]:
    """(used MB, total MB) for the first GPU, or zeros."""
    out = text(run(("nvidia-smi", "--query-gpu=memory.used,memory.total",
               "--format=csv,noheader,nounits")))
    line = out.strip().splitlines()[0] if out.strip() else ""
    try:
        used, total = (float(x.strip()) for x in line.split(","))
        return used, total
    except (ValueError, IndexError):
        return 0.0, 0.0


def vram_used_pct(_root: str, _arg: int = 0) -> float:
    used, total = gpu()
    return (used / total * 100.0) if total else 0.0


def path_exists(_root: str, path: str) -> float:
    return 1.0 if os.path.exists(os.path.expandvars(path)) else 0.0


def path_age_days(_root: str, path: str) -> float:
    p = os.path.expandvars(path)
    try:
        return (time.time() - os.path.getmtime(p)) / 86400.0
    except OSError:
        return float("inf")


def dir_size_mb(_root: str, path: str) -> float:
    p = os.path.expandvars(path)
    total = 0
    try:
        for dirpath, _, filenames in os.walk(p):
            for fn in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass
            if total > 200 * 1024**3:
                break
    except OSError:
        return 0.0
    return total / 1024**2


# The only names a rule may use. Anything not here cannot be reached from the
# rule table, which is what keeps rules declarative rather than quietly
# becoming code again.
PROBES = {
    "exists": exists,
    "absent": absent,
    "count": count,
    "content": content,
    "bytes_over": bytes_over,
    "age_days": age_days,
    "json_key": json_key,
    "process_running": process_running,
    "port_open": port_open,
    "disk_free_pct": disk_free_pct,
    "vram_used_pct": vram_used_pct,
    "path_exists": path_exists,
    "path_age_days": path_age_days,
    "dir_size_mb": dir_size_mb,
    # grammar
    "longest_function": longest_function,
    "deep_nesting": deep_nesting,
    "py_smell": py_smell,
    "wide_signatures": wide_signatures,
    "unused_privates": unused_privates,
    "untyped_share": untyped_share,
    # history
    "churn": _history("churn"),
    "churn_acceleration": _history("churn_acceleration"),
    "fix_ratio": _history("fix_ratio"),
    "reverts": _history("reverts"),
    "hotspots": _history("hotspots"),
    "stagnant_days": _history("stagnant_days"),
    "new_code_share": _history("new_code_share"),
    "big_commits": _history("big_commits"),
    "fix_only_files": _history("fix_only_files"),
    "commits_in": _history("commits_in"),
    # expectation
    "version_disagreement": version_disagreement,
    "undeclared_deps": undeclared_deps,
    # identity
    "duplicate_files": duplicate_files,
    "stale_binaries": stale_binaries,
    # verdict
    "failing_test_share": failing_test_share,
    "log_errors": log_errors,
    # execution, via recorded runs
    "run_failed": run_failed,
    "run_unverified": run_unverified,
    # remote, via a local snapshot
    "vulnerable_deps": vulnerable_deps,
}


def clear_caches() -> None:
    for fn in (run, listing, processes, listening_ports, gpu):
        fn.cache_clear()
