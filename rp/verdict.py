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


def clear_caches() -> None:
    for fn in (test_totals, recent_log_errors):
        fn.cache_clear()
