"""VERDICT probes — conclusions another tool already reached.

The most parasitic kind there is: the work was done by something else and
written down as a by-product, and this reads the receipt. Test caches, coverage
files, linter output, review ledgers, build fingerprints.

The governing risk is **staleness, and it is the most dangerous failure in the
system** because the output looks perfectly current. A verdict is an opinion
about one snapshot; once the code moves past it, the opinion is not wrong so
much as about something else. Every probe here carries the age of its evidence
and refuses to answer when the evidence has expired.

A log is the sharpest case. An error written three weeks ago and since fixed
sits in the file forever, so counting lines reports solved problems as live
ones — this system's own log holds eleven, every one of them already diagnosed.
"""

from __future__ import annotations

import json
import os
import re
import time
from functools import lru_cache

DAY = 86400.0

# How far back a log line still describes the present. Beyond this an error is
# history, and history belongs to the HISTORY kind, not to a live verdict.
LOG_WINDOW_DAYS = 7.0

_TIMESTAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
_ERROR_LINE = re.compile(r"\b(ERROR|FATAL|PANIC|CRITICAL|Traceback)\b")
# Lines that contain an error word while reporting that nothing is wrong.
_NOT_AN_ERROR = re.compile(r"(?i)(0 errors?|no errors?|errors?:\s*0|error_count=0)")


@lru_cache(maxsize=256)
def uses_pytest(root: str) -> bool:
    """Is pytest actually this project's runner?

    A `.pytest_cache` proves only that pytest was *run* here once, which is not
    the same claim. One project in this fleet writes its tests as standalone
    scripts — documented in its README, run directly, all passing — and carries
    a stray cache from a single wrong-runner invocation months ago. That cache
    records a collection crash as five "failing" files, and the failing-test
    sensor believed it.

    Evidence of intent, not of a past invocation: a pytest config section, or a
    test file that actually defines pytest-style tests.
    """
    for name in ("pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"):
        path = os.path.join(root, name)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                if "[tool.pytest" in fh.read() or "[pytest]" in fh.read():
                    return True
        except OSError:
            continue
    for folder in ("tests", "test", "."):
        base = os.path.join(root, folder)
        if not os.path.isdir(base):
            continue
        try:
            for entry in os.scandir(base):
                if not (entry.is_file() and entry.name.startswith("test_")
                        and entry.name.endswith(".py")):
                    continue
                with open(entry.path, encoding="utf-8", errors="replace") as fh:
                    if re.search(r"^\s*(def test_|async def test_)", fh.read(), re.M):
                        return True
        except OSError:
            continue
    return False


@lru_cache(maxsize=256)
def test_totals(root: str) -> tuple[int, int]:
    """(failing files, total collected tests) from pytest's own cache.

    The denominator is the point. "Five files failing" and "five files failing
    of six" are different situations, and the first is what this system
    reported until the cache's `nodeids` was read alongside `lastfailed`.
    """
    base = os.path.join(root, ".pytest_cache", "v", "cache")
    failing = total = 0
    try:
        with open(os.path.join(base, "lastfailed"), encoding="utf-8") as fh:
            data = json.load(fh)
        failing = len({k.split("::", 1)[0] for k in data}) if isinstance(data, dict) else 0
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(base, "nodeids"), encoding="utf-8") as fh:
            ids = json.load(fh)
        total = len(ids) if isinstance(ids, list) else 0
    except (OSError, ValueError):
        pass
    return failing, total


def test_cache_age_days(root: str) -> float:
    path = os.path.join(root, ".pytest_cache", "v", "cache", "lastfailed")
    try:
        return (time.time() - os.path.getmtime(path)) / DAY
    except OSError:
        return float("inf")


def _line_time(line: str) -> float | None:
    m = _TIMESTAMP.search(line)
    if not m:
        return None
    try:
        return time.mktime((int(m[1]), int(m[2]), int(m[3]),
                            int(m[4]), int(m[5]), int(m[6]), 0, 0, -1))
    except (ValueError, OverflowError):
        return None


@lru_cache(maxsize=256)
def _last_commit(root: str) -> float:
    from .sensors import git
    stamp = git(root, "log", "-1", "--format=%ct").strip()
    try:
        return float(stamp)
    except ValueError:
        return 0.0


@lru_cache(maxsize=256)
def recent_log_errors(root: str, window_days: float = LOG_WINDOW_DAYS) -> tuple[int, str]:
    """(count, example) of error lines still describing the current code.

    Timestamped lines are dated individually; a log with no timestamps is
    judged by the file's own mtime, and if that is outside the window the whole
    file is skipped rather than counted.

    Two cutoffs, not one. The window keeps old errors from being reported
    forever — counting every error line in a log reports every problem ever
    solved as a current one. The commit cutoff is the same rule this system
    applies to test caches and reviews: an error written before the code
    changed is an opinion about older code. This module's own log holds a GL
    panic from three days before the fix landed, and without the second cutoff
    it would be reported as live indefinitely.
    """
    cutoff = max(time.time() - window_days * DAY, _last_commit(root))
    logs: list[str] = []
    for parent in (root, os.path.join(root, "logs")):
        if not os.path.isdir(parent):
            continue
        try:
            for entry in os.scandir(parent):
                if entry.is_file() and entry.name.endswith(".log"):
                    logs.append(entry.path)
        except OSError:
            continue

    count, example = 0, ""
    for path in logs[:12]:
        try:
            if os.path.getsize(path) > 20_000_000:
                continue
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        undated_ok = mtime >= cutoff
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not _ERROR_LINE.search(line) or _NOT_AN_ERROR.search(line):
                        continue
                    when = _line_time(line)
                    if when is None:
                        if not undated_ok:
                            continue
                    elif when < cutoff:
                        continue
                    count += 1
                    if not example:
                        example = line.strip()[:90]
        except OSError:
            continue
    return count, example


# --- recorded runs (the EXECUTION kind, read as a verdict) -------------------

# Receipts live with the tool, keyed by repository, never inside it: writing
# them into the wrapped project left untracked files that this system's own
# `wip` sensor would count as pressure. Defined here and imported by rpwrap.py
# so the writer and the reader cannot drift — they did, and a test caught it
# within a minute.
STORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "runs")


def receipts_path(root: str) -> str:
    """This repository's receipt file. Hashed so two projects with the same
    folder name cannot collide, named so the store stays human-readable."""
    import hashlib

    full = os.path.abspath(root).replace("\\", "/").rstrip("/")
    tag = hashlib.blake2b(full.encode(), digest_size=6).hexdigest()
    return os.path.join(STORE, f"{os.path.basename(full)}-{tag}.json")


@lru_cache(maxsize=256)
def runs(root: str) -> tuple[dict, ...]:
    """Recorded outcomes of commands someone ran through `rpwrap.py`.

    The field never executes anything. A person or a build system ran the
    command because they were going to anyway, the wrapper left a receipt, and
    this reads it — which keeps the probe a cheap local read and the kind
    parasitic.
    """
    path = receipts_path(root)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return ()
    return tuple(r for r in data if isinstance(r, dict))


def last_run(root: str, label: str) -> dict | None:
    matching = [r for r in runs(root) if r.get("label") == label]
    return matching[-1] if matching else None


def run_state(root: str, label: str) -> tuple[str, dict | None]:
    """('pass' | 'fail' | 'stale' | 'never', record).

    `stale` is the answer that matters. A wrapper that stops being used — the
    build moves to an IDE, the habit lapses — writes no new receipt, and the
    last one goes on saying whatever it said. Absence of a recorded failure is
    not evidence of a success, so a receipt taken at a commit that is no longer
    HEAD is reported as stale rather than as a pass.
    """
    record = last_run(root, label)
    if record is None:
        return "never", None
    head = _head_sha(root)
    if head and record.get("head") and record["head"] != head:
        return "stale", record
    if time.time() - record.get("at", 0) > 14 * DAY:
        return "stale", record
    return ("fail" if record.get("code") else "pass"), record


@lru_cache(maxsize=256)
def _head_sha(root: str) -> str:
    from .sensors import git
    return git(root, "rev-parse", "HEAD").strip()


def clear_caches() -> None:
    # Tolerant of substitution: a caller that has replaced one of these with a
    # stub still gets the rest cleared, rather than an AttributeError halfway
    # through leaving the others stale.
    for name in ("test_totals", "recent_log_errors", "runs", "_head_sha",
                 "_last_commit"):
        fn = globals().get(name)
        clear = getattr(fn, "cache_clear", None)
        if clear:
            clear()
