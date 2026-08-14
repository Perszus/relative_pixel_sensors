"""The sensor set.

Every sensor here obeys the same three rules:

  * **It taps a verdict that already exists.** Nothing scans the fleet for the
    map's benefit. git's index, git's status, and Ester's reports are all
    produced whether or not anything reads them; these functions read them.
  * **It declares its statement type.** An event accumulates and decays; a
    standing statement is set and must clear; a state is the current answer and
    replaces the last one. Collapsing the three was the real bug in 1.0.
  * **It answers for the whole fleet at once.** Clearing is the half that gets
    forgotten, and a sensor can only retract a finding it no longer sees if it
    reports its complete picture every pass (see `Field.apply_state`).

The masses below are guesses. They decide how loudly each sensor speaks
relative to the others, and nothing has calibrated them yet -- that is what
Stage 2 is for. They are gathered here rather than scattered so that
recalibrating is one edit.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from dataclasses import dataclass

from . import probes

# --- how loudly each sensor speaks ------------------------------------------
W_TODO = 0.5          # per debt marker
W_FIX = 1.0           # per file touched by a repair commit
W_ACTIVITY = 1.0      # per file touched by any commit
W_WIP = 1.5           # per uncommitted file
W_UNPUSHED = 2.0      # per commit that exists only here
W_ESTER_SEV = {"1": 4.0, "2": 2.0, "3": 1.0}
W_CLEAN = 1.5         # per explicit clean verdict
W_TESTED = 12.0       # x the test-file ratio
# Deliberately small. Every project here has a README, so this was 43% of the
# entire health channel while separating nothing -- a signal every subject
# scores full marks on is not measuring anything.
W_DOCS = 1.0
W_TEST_FAIL = 3.0     # per test file failing at last run
W_CI = 4.0            # for having automated checks at all
W_LOCK_DRIFT = 2.0    # per manifest that outran its lockfile
W_HEAVY = 1.0         # per oversized file committed
W_CONFLICT = 6.0      # per file with committed conflict markers
W_GIANT = 1.0         # per outsized source file
W_SECRET = 8.0        # per credential-shaped literal
W_DOC_DRIFT = 1.5     # scaled by how far behind
W_SUGGESTION = 0.5    # per open suggestion; a good idea is not a defect

# A tracked file bigger than this is almost never source.
HEAVY_BYTES = 2_000_000
# A source file bigger than this has stopped being one idea.
GIANT_BYTES = 80_000

LOCKFILES = {
    "Cargo.toml": "Cargo.lock",
    "pubspec.yaml": "pubspec.lock",
    "package.json": "package-lock.json",
    "pyproject.toml": "poetry.lock",
}

SOURCE_EXT = ("*.py", "*.rs", "*.dart", "*.kt", "*.java", "*.cpp", "*.cc",
              "*.h", "*.hpp", "*.ts", "*.tsx", "*.js", "*.go", "*.rb", "*.cs")
SOURCE_SUFFIX = tuple(e.lstrip("*") for e in SOURCE_EXT)

DEBT_RE = r"TODO|FIXME|HACK|XXX"

# What a commit subject looks like when it is repairing rather than building.
# Shared so the history probes and the activity sensor agree about what counts
# as a fix — two definitions of that would make their numbers incomparable.
FIX_WORDS = ("fix", "bug", "crash", "regress", "broke", "broken", "revert",
             "hotfix", "patch", "leak", "deadlock", "workaround")
FIX_HINT = re.compile("|".join(FIX_WORDS), re.I)

# Code that is tracked but not ours to act on. Excluded from *sizing* the
# regions, so it never earns a region of its own -- six regions of vendored
# audio library are six places attention can be pulled to and can do nothing
# about. Signals from these paths still land, on the nearest real ancestor.
NOT_OURS = ("third_party", "thirdparty", "vendor", "vendored", "node_modules",
            "archive", "archived", "legacy", "generated", "external", ".dart_tool",
            "backup", "backups", "old", "deprecated")


def is_ours(path: str) -> bool:
    return not any(seg in NOT_OURS for seg in path.replace("\\", "/").lower().split("/"))


@dataclass(frozen=True)
class Spec:
    """One row of the sensor spec sheet from 2.0 sec.4.3."""
    name: str
    channel: str        # R pressure / G health / B activity
    kind: str           # event | standing | state
    scope: str          # file | project
    clears: str
    doc: str


REGISTRY: tuple[Spec, ...] = (
    Spec("commits", "B", "event", "file", "decays",
         "Files touched by any commit. What is being worked on."),
    Spec("fixes", "R", "event", "file", "decays",
         "Files touched by a commit whose subject reads like a repair."),
    Spec("wip", "B", "standing", "file", "when the file is committed",
         "Uncommitted changes sitting in the working tree right now."),
    Spec("unpushed", "R", "standing", "project", "when pushed",
         "Commits that exist only on this machine. Work not yet backed up."),
    Spec("todo", "R", "standing", "file", "when the marker is deleted",
         "TODO / FIXME / HACK / XXX markers in tracked source."),
    Spec("ester_open", "R", "standing", "file", "when the finding is closed",
         "Ester's open findings, weighted by severity."),
    Spec("ester_clean", "G", "standing", "file", "when superseded",
         "Ester's explicit clean verdicts. Reviewed and found sound."),
    Spec("tested", "G", "state", "project", "recomputed each pass",
         "Share of tracked source files that are tests."),
    Spec("documented", "G", "state", "project", "recomputed each pass",
         "Whether the project has a README and a docs directory."),
    Spec("tests_failing", "R", "standing", "file", "when the suite passes",
         "Tests failing at their last run. Ignored if the verdict predates the code."),
    Spec("ci", "G", "state", "project", "recomputed each pass",
         "Whether anything checks this project automatically."),
    Spec("lock_drift", "R", "standing", "project", "when the lockfile is regenerated",
         "A dependency manifest committed later than its lockfile."),
    Spec("heavy_files", "R", "standing", "file", "when the file is removed",
         f"Tracked files over {HEAVY_BYTES // 1_000_000} MB. Usually committed by mistake."),
    Spec("conflicts", "R", "standing", "file", "when the markers are removed",
         "Committed merge-conflict markers. Unambiguously broken."),
    Spec("giants", "R", "standing", "file", "when the file is split",
         f"Source files over {GIANT_BYTES // 1000} KB. Too big to hold in one head."),
    Spec("secrets", "R", "standing", "file", "when the literal is removed",
         "Credential-shaped literals committed to source."),
    Spec("doc_drift", "R", "standing", "project", "when docs are updated",
         "README left behind by the code it describes."),
    Spec("suggestions", "G", "standing", "project", "when acted on or dropped",
         "Ester's idea ledger — improvements offered and not yet taken up."),
)


def verdict_age(repo: str, artifact: str) -> dict | None:
    """How far the code has moved since this verdict was formed.

    A test run, a review, a lint pass -- each is an opinion about one snapshot.
    If the code moved afterwards the opinion is not wrong so much as *about
    something else*, and reporting it as current is how a field starts lying
    confidently. One project's failing-test record was seventeen days stale when
    this was written; without the check, those five failures would have been
    published as live pressure.

    Returns days elapsed and commits landed since, or None if the artifact is
    missing. Deliberately a measure rather than a verdict: "the code moved at
    all" is true of everything all the time and so distinguishes nothing --
    the first version of this test flagged all eight projects, which is a
    horoscope, not a signal.
    """
    try:
        mtime = os.path.getmtime(artifact)
    except OSError:
        return None
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime))
    raw = git(repo, "rev-list", "--count", f"--since={since}", "HEAD").strip()
    try:
        commits = int(raw)
    except ValueError:
        commits = 0
    return {"days": (time.time() - mtime) / 86400.0, "commits_since": commits}


# A verdict survives a little drift. These are the points past which it stops
# describing the code it was formed about.
STALE_DAYS = 3.0
STALE_COMMITS = 10


def git(repo: str, *args: str, timeout: int = 90) -> str:
    try:
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                           text=True, timeout=timeout, shell=False)
        return p.stdout if p.returncode == 0 else ""
    except Exception:
        return ""


def _add(bucket: dict, group: str, value: float, key: str | None = None) -> None:
    v, keys = bucket.get(group, (0.0, {}))
    keys = dict(keys)
    if key:
        keys[key] = keys.get(key, 0.0) + value
    bucket[group] = (v + value, keys)


# --- file-scope sensors ------------------------------------------------------

def wip(repo: str, router) -> dict:
    """Uncommitted work. The single most useful thing to know on arrival:
    whether someone is mid-thought in a file, or the tree is clean."""
    out: dict = {}
    for line in git(repo, "status", "--porcelain").splitlines():
        line = line.rstrip()
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        # Renames read "old -> new"; the new name is the one that exists.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        g, rel = router.route_and_key(f"{repo}/{path}")
        if g:
            _add(out, g, W_WIP, rel)
    return out


def todo(repo: str, router) -> dict:
    """Debt markers the authors left for themselves.

    Uses `git grep`, which reads the index rather than walking the tree, and is
    scoped to tracked source files so vendored trees cannot flood it.
    """
    out: dict = {}
    args = ["grep", "-I", "-c", "-E", DEBT_RE, "--", *SOURCE_EXT]
    for line in git(repo, *args).splitlines():
        if ":" not in line:
            continue
        path, _, count = line.rpartition(":")
        # Vendored code is full of other people's TODOs. Counting them was
        # reporting 38 markers against one project that all belonged to a
        # vendored audio library -- a number not false so much as about
        # somebody else, which is worse.
        if not is_ours(path):
            continue
        try:
            n = int(count)
        except ValueError:
            continue
        g, rel = router.route_and_key(f"{repo}/{path}")
        if g:
            _add(out, g, W_TODO * n, rel)
    return out


def ester(repo: str, router) -> tuple[dict, dict]:
    """Ester's ledger, both halves.

    Open findings press on R. Her explicit `> clean` verdicts lift G, which is
    the only sensor in the set that reports *good* news from an actual
    judgement rather than from the absence of bad news.
    """
    path = os.path.join(repo, "ester_analysis.md")
    if not os.path.isfile(path):
        return {}, {}
    findings: dict = {}
    clean: dict = {}
    weight = 1.0
    is_open = False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if s.startswith("### "):
                is_open = "[OPEN]" in s
                weight = 1.0
                if "sev " in s:
                    weight = W_ESTER_SEV.get(s.split("sev ", 1)[1].strip()[:1], 1.0)
            elif s.startswith("area:") and is_open:
                area = s[5:].strip().split(":")[0].strip()
                if area and not area.startswith("<") and is_ours(area):
                    g, rel = router.route_and_key(f"{repo}/{area}")
                    if g:
                        _add(findings, g, weight, rel)
                is_open = False
            elif s.startswith("> clean"):
                # "> clean [date] path/to/file.rs — why it is fine"
                body = s[7:].strip().lstrip("]").strip()
                token = ""
                for part in body.replace("]", " ").split():
                    if part.endswith(SOURCE_SUFFIX):
                        token = part
                        break
                if token:
                    g, rel = router.route_and_key(f"{repo}/{token}")
                    if g:
                        _add(clean, g, W_CLEAN, rel)
    return findings, clean


# --- project-scope sensors ---------------------------------------------------

def unpushed(repo: str, label: str) -> dict:
    """Commits that exist only on this machine.

    Project-scope because it is a property of the branch, not of any file. Silent
    when there is no upstream: a repo that was never meant to be pushed is not
    carrying risk, and saying otherwise would train the reader to ignore it.
    """
    out = git(repo, "rev-list", "--count", "--left-right", "@{u}...HEAD").split()
    if len(out) != 2:
        return {}
    try:
        ahead = int(out[1])
    except ValueError:
        return {}
    if ahead <= 0:
        return {}
    return {label: (W_UNPUSHED * ahead, {f"{ahead} unpushed commit(s)": float(ahead)})}


def structure(
    repo: str, label: str, all_files: list[str], last_commit: float | None
) -> tuple[dict, dict, dict]:
    """Shape of the project, from git's index. Returns (tested, documented, meta).

    `git ls-files` is the whole cost: no directory walk, no stat calls, and it
    reflects what is actually tracked rather than whatever build output happens
    to be lying around.

    Test share is deliberately computed per PROJECT rather than per group. Tests
    usually live in a directory of their own, so a per-group ratio would report
    the tests directory as immaculate and the code it covers as untested, which
    is exactly backwards.
    """
    if not all_files:
        return {}, {}, {}

    # Ours only. Counting vendored code made one project read as 212 untested source
    # files when most of them belong to an audio library nobody here wrote, and
    # made its dominant language "h" for the same reason.
    files = [f for f in all_files if is_ours(f)]
    vendored = len(all_files) - len(files)
    src = [f for f in files if f.endswith(SOURCE_SUFFIX)]
    tests = [f for f in src if _is_test(f)]
    ratio = len(tests) / len(src) if src else 0.0

    has_readme = any(os.path.basename(f).lower().startswith("readme") for f in files)
    has_docs = any(f.lower().startswith("docs/") for f in files)

    tested = {}
    if src:
        tested = {label: (W_TESTED * ratio,
                          {f"{len(tests)}/{len(src)} source files are tests": ratio})}
    documented = {}
    if has_readme and has_docs:
        documented = {label: (W_DOCS, {"README + docs/": 1.0})}
    elif has_readme:
        documented = {label: (W_DOCS * 0.5, {"README only": 1.0})}

    # Whether anything has ever reviewed this project, and whether that review
    # still describes the current code. Without both, "no findings here" is
    # ambiguous between sound, unexamined, and examined-long-ago, and the field
    # would quietly report all three as the first.
    report = os.path.join(repo, "ester_analysis.md")
    reviewed = os.path.isfile(report)
    rev_age = verdict_age(repo, report) if reviewed else None
    review_drift = rev_age["commits_since"] if rev_age else 0
    review_current = reviewed and review_drift <= STALE_COMMITS

    meta = {
        "files": len(files),
        "vendored": vendored,
        "source": len(src),
        "tests": len(tests),
        "kind": _dominant_kind(src),
        "last_commit": last_commit,
        "reviewed": reviewed,
        "review_current": review_current,
        "review_drift": review_drift,
    }
    return tested, documented, {label: meta}


def tests_failing(repo: str, router, last_commit: float | None) -> tuple[dict, dict]:
    """pytest's own record of what failed last run. Returns (per_group, note).

    `.pytest_cache/v/cache/lastfailed` is written whether or not anything reads
    it, is gitignored, and is exactly the verdict we want. It is also the
    sharpest illustration of why `verdict_is_current` exists: the file happily
    survives months after the code under it changed.
    """
    path = os.path.join(repo, ".pytest_cache", "v", "cache", "lastfailed")
    if not os.path.isfile(path):
        return {}, {}
    # A cache is evidence that pytest ran, not that pytest is the runner. See
    # verdict.uses_pytest — a stray one made a passing suite look broken.
    from .verdict import uses_pytest
    if not uses_pytest(repo):
        return {}, {"tests_verdict": "not a pytest project — cache ignored"}
    age = verdict_age(repo, path)
    if age and (age["days"] > STALE_DAYS or age["commits_since"] > STALE_COMMITS):
        n = age["commits_since"]
        return {}, {"tests_verdict":
                    f"stale ({age['days']:.0f}d old, {n} commit"
                    f"{'' if n == 1 else 's'} since)"}
    try:
        with open(path, encoding="utf-8") as fh:
            failed = json.load(fh)
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(failed, dict) or not failed:
        return {}, {"tests_verdict": "passing"}

    out: dict = {}
    files = {k.split("::", 1)[0] for k in failed}
    for f in files:
        g, rel = router.route_and_key(f"{repo}/{f}")
        if g:
            _add(out, g, W_TEST_FAIL, rel)
    return out, {"tests_verdict": f"{len(files)} file(s) failing"}


def ci(repo: str, label: str, files: list[str]) -> dict:
    """Does anything check this project without being asked?"""
    has = any(f.startswith(".github/workflows/") for f in files) or any(
        f in (".gitlab-ci.yml", "azure-pipelines.yml", ".circleci/config.yml")
        for f in files
    )
    if not has:
        return {}
    return {label: (W_CI, {"automated checks configured": 1.0})}


def lock_drift(repo: str, label: str, files: list[str]) -> dict:
    """A manifest committed after its lockfile: declared dependencies and
    resolved dependencies have diverged, and the build is running on the old
    answer."""
    fset = set(files)
    drifted = {}
    for manifest, lock in LOCKFILES.items():
        if manifest not in fset or lock not in fset:
            continue
        mt = git(repo, "log", "-1", "--format=%ct", "--", manifest).strip()
        lt = git(repo, "log", "-1", "--format=%ct", "--", lock).strip()
        if mt and lt and float(mt) > float(lt) + 60:
            days = (float(mt) - float(lt)) / 86400.0
            drifted[f"{manifest} is {days:.0f}d ahead of {lock}"] = 1.0
    if not drifted:
        return {}
    return {label: (W_LOCK_DRIFT * len(drifted), drifted)}


# A credential-shaped LITERAL, not a variable called `token`. The first version
# of this matched `token = credentials.credentials` and `_hasPassword =
# MutableStateFlow(...)` -- 100% false positives across six repos, which is how
# a sensor teaches you to ignore it.
_SECRET_RE = re.compile(
    r"""(?ix)
    (api[_-]?key|secret|passwd|password|auth[_-]?token|access[_-]?token
     |bearer|private[_-]?key|client[_-]?secret)
    \s* [:=] \s*
    ["']([A-Za-z0-9/+=_.\-]{20,})["']
    """)

# Values that are shaped like secrets and are not. Placeholders are the common
# case in exactly the files this sensor looks at.
_NOT_A_SECRET = re.compile(
    r"(?i)^(x{4,}|y{4,}|\.{3,}|<.*>|\$\{.*\}|%\w+%|change[_-]?me|your[_-]?\w+"
    r"|example|placeholder|dummy|sample|test|todo|none|null|true|false"
    r"|[0-9.]+)$")


def _entropy(s: str) -> float:
    """Shannon entropy per character. A real key is close to random; an
    identifier, a path or a version string is not."""
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def secrets(repo: str, router) -> dict:
    """Credential-shaped literals committed to source.

    Normally silent, like `conflicts` — and silence is the expected answer.
    Weighted heavily because the consequence is not "some rework".
    """
    out: dict = {}
    args = ["grep", "-I", "-n", "-E", "-i",
            r"(api[_-]?key|secret|passwd|password|token|bearer|private[_-]?key)"
            r"[[:space:]]*[:=][[:space:]]*[\"'][A-Za-z0-9/+=_.-]{20,}[\"']",
            "--", *SOURCE_EXT, "*.json", "*.yaml", "*.yml", "*.toml",
            "*.env", "*.cfg", "*.ini"]
    for raw in git(repo, *args).splitlines():
        path, _, rest = raw.partition(":")
        _, _, line = rest.partition(":")
        if not path or not is_ours(path):
            continue
        m = _SECRET_RE.search(line)
        if not m:
            continue
        # Honour the same suppression marker the declarative rules use.
        #
        # This sensor and the `secret-literal` rule detect the same thing by
        # two code paths, and only one of them was checking. The result was
        # this repo's own test fixture -- a deliberately fake credential,
        # already marked -- reported as a committed secret on every single
        # pass, which is precisely how a detector teaches a reader to stop
        # believing it. Imported rather than re-spelled so the two paths
        # cannot disagree again.
        if probes.ALLOW_MARKER in line:
            continue
        value = m.group(2)
        if _NOT_A_SECRET.match(value) or _entropy(value) < 3.2:
            continue
        g, rel = router.route_and_key(f"{repo}/{path}")
        if g:
            _add(out, g, W_SECRET, f"{rel} ({m.group(1).lower()})")
    return out


def doc_drift(repo: str, label: str, files: list[str]) -> dict:
    """How far the README has fallen behind the code it describes.

    Scaled rather than binary: a README twenty commits old is aging, one that
    has not moved in two hundred is describing a different program.
    """
    if "README.md" not in set(files):
        return {}
    stamp = git(repo, "log", "-1", "--format=%ct", "--", "README.md").strip()
    if not stamp:
        return {}
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(stamp)))
    raw = git(repo, "rev-list", "--count", f"--since={since}", "HEAD").strip()
    try:
        behind = int(raw)
    except ValueError:
        return {}
    if behind < 25:
        return {}
    weight = W_DOC_DRIFT * min(behind / 25.0, 4.0)
    return {label: (weight, {f"README is {behind} commits behind": float(behind)})}


def suggestions(repo: str, label: str) -> dict:
    """Ester's idea ledger, which nothing was reading.

    Lands on health rather than pressure, and deliberately weighted low: an
    unacted-on good idea is not a defect. What it measures is that someone
    looked and had something to say -- forty-seven of these were sitting
    unread across fourteen repos.
    """
    path = os.path.join(repo, "ester_suggestions.md")
    if not os.path.isfile(path):
        return {}
    n = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.strip().startswith("### ") and "[OPEN]" in line:
                n += 1
    if not n:
        return {}
    return {label: (W_SUGGESTION * n, {f"{n} open suggestion(s)": float(n)})}


def conflicts(repo: str, router) -> dict:
    """Merge-conflict markers that made it into a commit.

    The least ambiguous signal in the whole set: there is no reading of a
    committed `<<<<<<<` that is fine. Weighted heavily for the same reason, and
    normally silent, which is exactly what a sensor for a rare disaster should
    be.
    """
    out: dict = {}
    args = ["grep", "-I", "-l", "-E", r"^<{7} |^>{7} ", "--", *SOURCE_EXT]
    for path in git(repo, *args).splitlines():
        path = path.strip()
        if not path or not is_ours(path):
            continue
        g, rel = router.route_and_key(f"{repo}/{path}")
        if g:
            _add(out, g, W_CONFLICT, rel)
    return out


def giants(repo: str, router, files: list[str]) -> dict:
    """Source files that have outgrown being one idea.

    A crude proxy -- bytes, not complexity -- but it costs nothing beyond the
    stat already being done for `heavy_files`, and it is right often enough to
    be worth a line. Deliberately weak: this is a hint, not a verdict.
    """
    out: dict = {}
    for f in files:
        if not f.endswith(SOURCE_SUFFIX) or not is_ours(f):
            continue
        try:
            size = os.path.getsize(os.path.join(repo, f))
        except OSError:
            continue
        if size < GIANT_BYTES:
            continue
        g, rel = router.route_and_key(f"{repo}/{f}")
        if g:
            _add(out, g, W_GIANT, f"{rel} ({size/1000:.0f} KB)")
    return out


def heavy_files(repo: str, router, files: list[str]) -> dict:
    """Tracked files far too big to be source. Almost always a mistake that
    nobody noticed, and they are permanent once committed."""
    out: dict = {}
    for f in files:
        try:
            size = os.path.getsize(os.path.join(repo, f))
        except OSError:
            continue
        if size < HEAVY_BYTES:
            continue
        g, rel = router.route_and_key(f"{repo}/{f}")
        if g:
            _add(out, g, W_HEAVY, f"{rel} ({size/1e6:.0f} MB)")
    return out


def _is_test(path: str) -> bool:
    p = path.lower().replace("\\", "/")
    parts = p.split("/")
    if any(seg in ("test", "tests", "spec", "__tests__") for seg in parts[:-1]):
        return True
    base = parts[-1]
    return (
        base.startswith("test_")
        or base.endswith(("_test.py", "_test.dart", "_test.go", "_test.rs",
                          "_test.kt", ".test.ts", ".test.js", ".spec.ts"))
    )


# Headers accompany an implementation language; they are not one. Counting them
# made a Kotlin/C++ Android app report its kind as "h".
_HEADER_EXT = ("h", "hpp", "hh", "hxx", "d.ts")


def _dominant_kind(src: list[str]) -> str:
    counts: dict[str, int] = {}
    for f in src:
        ext = os.path.splitext(f)[1].lstrip(".").lower()
        if ext:
            counts[ext] = counts.get(ext, 0) + 1
    if not counts:
        return "?"
    bodies = {e: n for e, n in counts.items() if e not in _HEADER_EXT}
    return max(bodies or counts, key=(bodies or counts).__getitem__)


def age_desc(epoch: float | None) -> str:
    if not epoch:
        return "never"
    d = (time.time() - epoch) / 86400.0
    if d < 1:
        return "today"
    if d < 2:
        return "yesterday"
    if d < 60:
        return f"{d:.0f}d ago"
    return f"{d/30.0:.0f}mo ago"
