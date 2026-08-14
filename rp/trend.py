"""Trends in ambient values.

The rest of the field treats ambient readings as levels: true now, replaced
next pass, meaningless as history. That is right for answering *how full is
the disk* -- and it is exactly why the instrument could not answer the more
useful question, *is it filling*.

The distinction matters because it is the whole justification for this layer.
A volume's current free space is one click away in any file manager, so
reporting it spends a reader's attention on something they already had. How
much it moved since yesterday is not visible from anywhere without something
having written the earlier number down, which is what this module does.

Deliberately tiny and separate: a handful of numbers per volume, kept only
long enough to subtract.
"""

from __future__ import annotations

import json
import os
import time

# Readings older than this cannot answer "recently", and keeping them would
# make a slow month-long slide look like a sudden drop.
WINDOW_SECS = 7 * 24 * 3600

# Enough to survive Sentinel's ten-minute tick for a week without growing
# without bound.
MAX_POINTS = 400


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # A corrupt or absent history costs a trend, not a pass.
        return {}


def save(path: str, history: dict) -> None:
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(history, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        pass


def record(history: dict, key: str, value: float, now: float | None = None) -> dict:
    """Append a reading and forget anything too old to be useful."""
    now = time.time() if now is None else now
    points = [p for p in history.get(key, []) if isinstance(p, list) and len(p) == 2]
    points.append([now, round(float(value), 3)])
    cutoff = now - WINDOW_SECS
    points = [p for p in points if p[0] >= cutoff][-MAX_POINTS:]
    history[key] = points
    return history


def delta(history: dict, key: str, now: float | None = None) -> tuple[float, float] | None:
    """Change since the oldest retained reading, and how long that spans.

    Returns `(change, hours)`, or None when there is not enough history to say
    anything. None is not zero: a first-ever reading has no trend, and
    reporting one as "stable" would be inventing a fact.
    """
    now = time.time() if now is None else now
    points = history.get(key) or []
    if len(points) < 2:
        return None
    oldest_t, oldest_v = points[0]
    _, latest_v = points[-1]
    hours = (now - oldest_t) / 3600.0
    # Two readings a minute apart cannot describe a day's drift.
    if hours < 1.0:
        return None
    return latest_v - oldest_v, hours


def falling(history: dict, key: str, drop: float, now: float | None = None) -> tuple[float, float] | None:
    """The reading has fallen by at least `drop` within the window."""
    change = delta(history, key, now)
    if change is None:
        return None
    moved, hours = change
    return (-moved, hours) if -moved >= drop else None
