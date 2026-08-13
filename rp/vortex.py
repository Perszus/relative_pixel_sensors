"""Core state: lazily-decayed multi-timescale accumulators.

The whole model is one idea repeated: a value that is a function of time rather
than a thing that is updated. Nothing ticks. Nothing is scheduled. Reading at
time t applies the decay that "happened" since the last write.

Design note (deviation from experiments.md M3):
    The 2.0 document describes the vortex as rings that FOLD into each other at
    doubling timescales -- RRD style. Implementing it that way requires fold
    triggers, boundary handling, and the question "is this datum counted once?".
    All of that disappears if the rings are instead INDEPENDENT accumulators at
    different half-lives, each receiving every event. Same bounded mass, same
    O(1) ingest, same temporal gradient, but no folding, no boundaries, and no
    possible double-count. M3 is therefore reformulated: see run_experiments.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Ring half-lives in seconds: hour, day, week, month, year.
RING_HALFLIVES = (3600.0, 86400.0, 604800.0, 2592000.0, 31536000.0)
RING_NAMES = ("hour", "day", "week", "month", "year")
N_RINGS = len(RING_HALFLIVES)

# lambda_i such that value halves every RING_HALFLIVES[i] seconds.
_LAMBDAS = tuple(math.log(2.0) / h for h in RING_HALFLIVES)

# Below this, a value is indistinguishable from never-having-happened.
EPSILON = 1e-9

# Bounded per-channel memory of which keys contributed. Bounded on purpose:
# unbounded key sets would make state O(files touched), violating invariant 4.
TOP_K = 8


def _decay(value: float, lam: float, dt: float) -> float:
    if value == 0.0:
        return 0.0
    if dt <= 0.0:
        return value
    out = value * math.exp(-lam * dt)
    return out if out > EPSILON else 0.0


@dataclass
class Channel:
    """One channel (R/G/B) of one group. Five floats, one timestamp, a small dict.

    `t is None` means never written. It must NOT be 0.0: epoch zero is a legal
    timestamp, and using it as a sentinel silently returns "cold" for any group
    whose first event lands there. That bug faked three passes in the first run.
    """

    rings: list[float] = field(default_factory=lambda: [0.0] * N_RINGS)
    t: float | None = None
    top: dict[str, float] = field(default_factory=dict)  # key -> mass, decayed at ring[1]
    top_t: float | None = None
    total_events: int = 0  # bookkeeping only, never decayed

    # Standing statements: "12 open findings here" is true until it stops being
    # true. They must NOT decay -- an unfixed bug does not get better by being
    # ignored -- and must NOT accumulate, or every re-read would inflate them.
    # They are SET, not added, and re-derived from source each collection.
    # This is the statement type 2.0 sec.4.2 separates out and whose absence
    # sec.4.3 Q6 says kills the tool.
    #
    # Keyed by SENSOR, because several sensors legitimately press on the same
    # channel -- open findings, unpushed work and TODO debt are all pressure --
    # and a single slot would let whichever ran last erase the others.
    levels: dict[str, float] = field(default_factory=dict)
    level_keys: dict[str, dict[str, float]] = field(default_factory=dict)
    level: float = 0.0  # cached sum of `levels`; kept in step by set_level

    # -- reads -------------------------------------------------------------

    def read(self, now: float) -> list[float]:
        """Decayed ring values at `now`. Pure: does not mutate.

        `now` must be >= the last write. Reading in the past would return a
        value containing events that had not happened yet, so it is clamped:
        the field has no opinion about times it has already passed.
        """
        if self.t is None:
            return [0.0] * N_RINGS
        dt = now - self.t
        if dt < 0.0:
            dt = 0.0
        return [_decay(v, lam, dt) for v, lam in zip(self.rings, _LAMBDAS)]

    def rates(self, now: float) -> list[float]:
        """Ring values converted to comparable event-rates.

        A stream of constant rate r reaches steady state value r/lambda in every
        ring, so multiplying back by lambda makes the rings directly comparable.
        A flat rate profile means "same intensity at every timescale".
        """
        return [v * lam for v, lam in zip(self.read(now), _LAMBDAS)]

    def magnitude(self, now: float) -> float:
        """Single scalar for Layer 1: decaying events plus standing judgments.

        The day ring is long enough to survive a lunch break and short enough to
        forget last month. The level is added undecayed, so a region with open
        findings never goes quiet just because nobody touched it lately -- which
        is precisely the "broken and abandoned" quadrant 2.0 sec.7 calls the
        highest-value reading.
        """
        return self.read(now)[1] + self.level

    def set_level(
        self, source: str, value: float, keys: dict[str, float] | None = None
    ) -> None:
        """Overwrite one sensor's standing statement, leaving the others alone.

        Re-derived from source every collection, so a closed finding clears
        itself by no longer being reported. Setting zero removes the sensor's
        contribution entirely rather than leaving a dead entry behind.
        """
        if value > 0.0:
            self.levels[source] = value
            self.level_keys[source] = dict(keys or {})
        else:
            self.levels.pop(source, None)
            self.level_keys.pop(source, None)
        self.level = sum(self.levels.values())

    def standing_by_source(self) -> dict[str, float]:
        return dict(self.levels)

    def standing_keys(self) -> dict[str, float]:
        """All standing pointers, flattened across sensors."""
        out: dict[str, float] = {}
        for keys in self.level_keys.values():
            for k, v in keys.items():
                out[k] = out.get(k, 0.0) + v
        return out

    # -- writes ------------------------------------------------------------

    def add(self, now: float, mass: float, key: str | None = None) -> None:
        """Composition on event. O(1) in history, always."""
        if self.t is None:
            self.t = now
        dt = now - self.t
        if dt < 0.0:
            # Clock went backwards. Do not decay into the future; absorb at the
            # stored time so the value can never be inflated by a bad clock.
            dt = 0.0
            now = self.t
        for i, lam in enumerate(_LAMBDAS):
            self.rings[i] = _decay(self.rings[i], lam, dt) + mass
        self.t = now
        self.total_events += 1
        if key is not None:
            self._touch_key(now, key, mass)

    def _touch_key(self, now: float, key: str, mass: float) -> None:
        lam = _LAMBDAS[1]
        if self.top_t is None:
            self.top_t = now
        dt = max(0.0, now - self.top_t)
        if dt > 0.0 and self.top:
            for k in list(self.top):
                v = _decay(self.top[k], lam, dt)
                if v == 0.0:
                    del self.top[k]
                else:
                    self.top[k] = v
        self.top_t = now
        self.top[key] = self.top.get(key, 0.0) + mass
        while len(self.top) > TOP_K:
            weakest = min(self.top, key=self.top.__getitem__)
            del self.top[weakest]

    # -- derived -----------------------------------------------------------

    def concentration(self, now: float) -> float:
        """0 = diffuse across many keys, 1 = all mass on one key.

        Normalised Herfindahl over the bounded top-k. Approximate by
        construction; it only ever has to separate "one file" from "everywhere".
        """
        if not self.top or self.top_t is None:
            return 0.0
        lam = _LAMBDAS[1]
        dt = max(0.0, now - self.top_t)
        vals = [_decay(v, lam, dt) for v in self.top.values()]
        total = sum(vals)
        if total <= 0.0:
            return 0.0
        return sum((v / total) ** 2 for v in vals)

    def pointers(self, now: float, n: int = 3) -> list[tuple[str, float]]:
        if self.top_t is None:
            return []
        lam = _LAMBDAS[1]
        dt = max(0.0, now - self.top_t)
        scored = [(k, _decay(v, lam, dt)) for k, v in self.top.items()]
        scored = [(k, v) for k, v in scored if v > 0.0]
        scored.sort(key=lambda kv: -kv[1])
        return scored[:n]

    # Rings used for classification: day / week / month.
    #
    # The hour ring and the year ring are stored (they cost 2 floats) but are
    # NOT classified on, for measured reasons:
    #   - hour: at the fleet's real signal volume (~16/day, see M8) the event
    #     interval exceeds the half-life, so the ring is a sawtooth between
    #     "just fired" and "nearly zero". It reports sampling phase, not load.
    #   - year: needs 2-3 half-lives to saturate, so it reads artificially low
    #     for the system's first several years and would make every steady
    #     workload look like it is warming.
    WORKING = (1, 2, 3)

    def effective_profile(self, now: float) -> str:
        """The reading a consumer should show.

        `profile` describes activity, which is the wrong axis when the reason a
        region is loud is that something is unresolved there. A region with open
        findings and no recent commits is temporally cooling and practically
        abandoned, and reporting the first hides the second.

        This exists so every consumer agrees. The serving layer applied the
        override for its own one-line marker while the stored profile did not,
        so the Sentinel pane displayed COOLING directly above "UNRESOLVED 13".
        """
        if self.level > 0.0 and self.level >= self.read(now)[1]:
            return "standing"
        return self.profile(now)

    def profile(self, now: float) -> str:
        """Temporal shape, read off the ring gradient.

        This is the claim that the rings buy something an instantaneous value
        cannot give: warming is invisible to any single number.
        """
        r = self.rates(now)
        if max(r) <= 0.0:
            # No activity at any timescale. If a judgment is still standing, the
            # region is not quiet -- it is abandoned, which is a louder reading.
            return "standing" if self.level > 0.0 else "cold"
        short, mid, long = (r[i] for i in self.WORKING)
        if long <= 0.0:
            return "spike" if short > 0.0 else "cold"

        span = short / long

        if span < 0.5:
            return "cooling"
        if span <= 2.0:
            return "sustained"

        # Rising. A burst dumps everything into the shortest working ring; a
        # genuine ramp lifts each ring roughly in proportion. So the shape of
        # the rise, not its size, is what separates them.
        steepness = (short / mid) if mid > 0.0 else float("inf")
        return "spike" if steepness > 4.0 else "warming"

    # -- persistence -------------------------------------------------------

    def to_json(self) -> dict:
        d = {
            "r": [round(v, 10) for v in self.rings],
            "t": self.t,
            "top": {k: round(v, 6) for k, v in self.top.items()},
            "tt": self.top_t,
            "n": self.total_events,
        }
        if self.levels:
            d["lv"] = round(self.level, 6)
            d["lvs"] = {s: round(v, 4) for s, v in self.levels.items()}
            d["lk"] = {
                s: {k: round(v, 4) for k, v in keys.items()}
                for s, keys in self.level_keys.items()
                if keys
            }
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Channel":
        c = cls()
        c.rings = list(d["r"])
        c.t = d["t"]
        c.top = dict(d.get("top", {}))
        c.top_t = d.get("tt")
        c.total_events = d.get("n", 0)
        c.levels = dict(d.get("lvs", {}))
        c.level_keys = {s: dict(k) for s, k in d.get("lk", {}).items()}
        c.level = sum(c.levels.values()) if c.levels else d.get("lv", 0.0)
        return c
