"""HISTORY probes — a temporal record.

Everything about change rather than state. This is the only kind that can
answer "is this getting worse", which no snapshot can: a region with forty
findings that had eighty last month and a region with forty that had ten are
the same picture and opposite trajectories.

The governing rule for this kind: **a history probe must state its window.**
"Ten commits" over a week and over a year are different facts, and a number
without its horizon is one nobody can compare to anything.

One log walk per repository serves every probe here. Parsing `git log` twice
was most of the cost of the kind before this module existed.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from functools import lru_cache

from .sensors import FIX_HINT, SOURCE_SUFFIX, git, is_ours

DAY = 86400.0


@dataclass(frozen=True)
class Commit:
    when: float
    subject: str
    files: tuple[str, ...]

    @property
    def is_fix(self) -> bool:
        return bool(FIX_HINT.search(self.subject))

    @property
    def is_revert(self) -> bool:
        low = self.subject.lower()
        return low.startswith("revert") or "this reverts commit" in low


# The longest window any probe asks for. One walk covers every shorter one, so
# this is the whole cost of the kind — keep it at what the rules actually need.
HORIZON = 190


def log(root: str, days: int = 180) -> tuple[Commit, ...]:
    """Commits in a window, with the files each touched.

    Derived by filtering a single walk rather than running one per window.
    Caching on (root, days) still meant a separate `git log` for 30, 60, 90 and
    180 days on every repository — four walks each across eighteen repos, which
    took a pass from three seconds to twenty.
    """
    if days >= HORIZON:
        return _walk(root)
    cutoff = time.time() - days * DAY
    return tuple(c for c in _walk(root) if c.when >= cutoff)


@lru_cache(maxsize=64)
def _walk(root: str) -> tuple[Commit, ...]:
    # `%x01` and `%x02` are git's own escapes, which git expands. Writing the
    # Python escapes instead put a literal NUL byte into an argument, which
    # cannot survive being passed to a process — every log came back empty and
    # every history probe reported zero, which reads as "nothing is wrong".
    raw = git(root, "log", f"--since={HORIZON} days ago",
              "--pretty=format:%x01%ct%x02%s", "--name-only")
    out: list[Commit] = []
    when, subject, files = 0.0, "", []
    for line in raw.splitlines():
        if line.startswith("\x01"):
            if when:
                out.append(Commit(when, subject, tuple(files)))
            stamp, _, subject = line[1:].partition("\x02")
            try:
                when = float(stamp)
            except ValueError:
                when = time.time()
            files = []
        elif line.strip():
            files.append(line.strip())
    if when:
        out.append(Commit(when, subject, tuple(files)))
    return tuple(out)


def _ours(commit: Commit) -> list[str]:
    return [f for f in commit.files if is_ours(f) and f.endswith(SOURCE_SUFFIX)]


# --------------------------------------------------------------- the probes


def commits_in(root: str, days: int) -> float:
    return float(len(log(root, days)))


def churn(root: str, days: int) -> float:
    """File-touches in the window. Volume of change, not number of changes."""
    return float(sum(len(_ours(c)) for c in log(root, days)))


def churn_acceleration(root: str, days: int = 30) -> float:
    """Churn in the last window over churn in the window before it.

    The payoff of the whole kind. Above 1 means the rate of change is rising,
    and rising change in a place that is already under pressure is the one
    reading that says *worsening* rather than *bad*.

    Returns 0 when there is no prior period to compare against — a project's
    first month is not an acceleration, and reporting it as one would make
    every new repository look like it was on fire.
    """
    now = time.time()
    recent = [c for c in log(root, days * 2) if c.when >= now - days * DAY]
    prior = [c for c in log(root, days * 2) if c.when < now - days * DAY]
    if not prior:
        return 0.0
    a = sum(len(_ours(c)) for c in recent)
    b = sum(len(_ours(c)) for c in prior)
    return (a / b) if b else 0.0


def fix_ratio(root: str, days: int) -> float:
    """Share of commits that read as repairs, as a percentage.

    High means time is going on correction rather than construction. It says
    nothing on its own about whether that is good — a stabilisation push and a
    project in trouble look identical here, which is why it is a gradient and
    not a verdict.
    """
    commits = log(root, days)
    if len(commits) < 10:
        return 0.0
    return sum(c.is_fix for c in commits) / len(commits) * 100.0


def reverts(root: str, days: int) -> float:
    """Commits that undo other commits. Each one is a change that shipped and
    should not have."""
    return float(sum(c.is_revert for c in log(root, days)))


def hotspots(root: str, days: int, threshold: int) -> float:
    """Files touched more than `threshold` times in the window.

    Change-proneness is the oldest defect predictor there is, and unlike most of
    what this system measures it needs no judgement about the code at all.
    """
    counts: dict[str, int] = {}
    for c in log(root, days):
        for f in _ours(c):
            counts[f] = counts.get(f, 0) + 1
    return float(sum(1 for n in counts.values() if n > threshold))


def stagnant_days(root: str, _unused: int = 0) -> float:
    """Days since anything was committed. Infinite for an empty history.

    Asks git directly rather than through the walk: a repository dormant for
    longer than the horizon has no commits inside it, and would otherwise
    report as never-committed.
    """
    stamp = git(root, "log", "-1", "--format=%ct").strip()
    if not stamp:
        return float("inf")
    try:
        return (time.time() - float(stamp)) / DAY
    except ValueError:
        return float("inf")


@lru_cache(maxsize=64)
def _added(root: str, days: int) -> frozenset[str]:
    """Files git records as ADDED in the window. Actual creation, not first
    sighting."""
    raw = git(root, "log", f"--since={days} days ago", "--diff-filter=A",
              "--name-only", "--pretty=format:")
    return frozenset(f.strip() for f in raw.splitlines()
                     if f.strip() and is_ours(f.strip())
                     and f.strip().endswith(SOURCE_SUFFIX))


def new_code_share(root: str, days: int) -> float:
    """Share of the source tree created inside the window, as a percentage.

    New code is not worse code, but it is code nothing has had time to find
    problems in, and the part least likely to be covered by a review that
    predates it.

    Uses git's own add-record. The first version inferred creation from "first
    appearance in the log window", which is not the same thing at all — every
    file touched after the window opened looked new, so this fired on all
    eighteen projects at the cap and distinguished nothing.
    """
    tracked = [f for f in git(root, "ls-files").splitlines()
               if f.strip() and is_ours(f) and f.strip().endswith(SOURCE_SUFFIX)]
    if len(tracked) < 10:
        return 0.0
    born = _added(root, days) & set(tracked)
    return len(born) / len(tracked) * 100.0


def big_commits(root: str, days: int, threshold: int) -> float:
    """Commits touching more files than a reviewer can hold at once."""
    return float(sum(1 for c in log(root, days) if len(c.files) > threshold))


def fix_only_files(root: str, days: int) -> float:
    """Files that appear in repair commits and never in any other kind.

    A file only ever touched to fix something is not being developed; it is
    being patched, which is a different and worse relationship.
    """
    fixes: dict[str, int] = {}
    others: dict[str, int] = {}
    for c in log(root, days):
        target = fixes if c.is_fix else others
        for f in _ours(c):
            target[f] = target.get(f, 0) + 1
    return float(sum(1 for f, n in fixes.items() if n >= 2 and f not in others))
