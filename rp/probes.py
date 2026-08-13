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
def run(cmd: tuple[str, ...], cwd: str | None = None, timeout: int = 60) -> str:
    """A command's stdout, or empty on any failure.

    Cached per pass: several rules ask the same question, and a rule set is
    supposed to be written without its author tracking who else already asked.
    """
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True,
                           timeout=timeout, shell=False, cwd=cwd)
        return p.stdout if p.returncode == 0 else ""
    except Exception:
        return ""


def git(root: str, *args: str) -> str:
    return run(("git", "-C", root, *args))


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


def content(root: str, pattern: str, regex: str) -> float:
    """Lines matching a regex across matching files.

    Reads through git's own grep when possible, which is index-backed and does
    not walk the tree.
    """
    if git(root, "rev-parse", "HEAD").strip():
        out = run(("git", "-C", root, "grep", "-I", "-c", "-E", regex, "--", pattern))
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
                total += sum(1 for line in fh if rx.search(line))
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


# --- machine ----------------------------------------------------------------


@lru_cache(maxsize=1)
def processes() -> tuple[str, ...]:
    """Running image names, lowercased."""
    out = run(("tasklist", "/fo", "csv", "/nh"))
    names = []
    for line in out.splitlines():
        if line.startswith('"'):
            names.append(line.split('","')[0].strip('"').lower())
    return tuple(names)


def process_running(_root: str, name: str) -> float:
    return 1.0 if name.lower() in processes() else 0.0


@lru_cache(maxsize=1)
def listening_ports() -> tuple[int, ...]:
    out = run(("netstat", "-ano", "-p", "TCP"))
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
    out = run(("nvidia-smi", "--query-gpu=memory.used,memory.total",
               "--format=csv,noheader,nounits"))
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
}


def clear_caches() -> None:
    for fn in (run, listing, processes, listening_ports, gpu):
        fn.cache_clear()
