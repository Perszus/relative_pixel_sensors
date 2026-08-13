"""The field: groups x channels, plus routing and persistence.

Invariants this file is responsible for:
  - ingest is O(1) in history and O(1) in group count
  - read is O(groups), never O(events)
  - resident state is O(groups), never O(files) or O(events)
  - nothing runs unless someone calls in
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from .vortex import Channel, N_RINGS, RING_NAMES

CHANNELS = ("R", "G", "B")  # pressure, health, activity

UNROUTED = "__unrouted__"


@dataclass
class Group:
    name: str
    channels: dict[str, Channel] = field(default_factory=lambda: {c: Channel() for c in CHANNELS})

    def to_json(self) -> dict:
        return {c: ch.to_json() for c, ch in self.channels.items()}

    @classmethod
    def from_json(cls, name: str, d: dict) -> "Group":
        g = cls(name)
        for c in CHANNELS:
            if c in d:
                g.channels[c] = Channel.from_json(d[c])
        return g


def _regions(
    files: list[str], max_files: int, min_files: int, max_depth: int
) -> list[str]:
    """Directory prefixes that make good regions, given the files under them.

    Splits a directory only when it is too big to be one place, and refuses to
    split out anything too small to be worth naming. The result is deliberately
    uneven in depth -- a flat project yields shallow regions and a deeply nested
    one yields deep regions, which is the point.
    """
    out: list[str] = []

    def walk(prefix: str, group: list[str], depth: int) -> None:
        if len(group) <= max_files or depth >= max_depth:
            if prefix:
                out.append(prefix)
            return
        children: dict[str, list[str]] = {}
        loose = 0
        head = len(prefix) + 1 if prefix else 0
        for f in group:
            rest = f[head:]
            if "/" in rest:
                children.setdefault(rest.split("/", 1)[0], []).append(f)
            else:
                loose += 1
        if not children:
            if prefix:
                out.append(prefix)
            return
        kept = 0
        for name, sub in children.items():
            if len(sub) < min_files:
                loose += len(sub)  # too thin to stand alone; parent keeps it
                continue
            kept += 1
            walk(f"{prefix}/{name}" if prefix else name, sub, depth + 1)
        # The prefix still needs a region of its own if anything stayed behind,
        # or if nothing could be split out at all.
        if prefix and (loose or kept == 0):
            out.append(prefix)

    walk("", files, 0)
    return out


class Router:
    """Path -> group. Longest-prefix wins.

    The join key is the file path, which is why routing is mostly derivable
    from the directory tree rather than authored by hand.
    """

    def __init__(self, rules: list[tuple[str, str]] | None = None):
        # (prefix, group). Stored normalised and sorted longest-first.
        self.rules: list[tuple[str, str]] = []
        for prefix, group in rules or []:
            self.add(prefix, group)
        self.unrouted_count = 0
        self.unrouted_samples: list[str] = []

    @staticmethod
    def _norm(p: str) -> str:
        return p.replace("\\", "/").strip("/").lower()

    def add(self, prefix: str, group: str) -> None:
        self.rules.append((self._norm(prefix), group))
        self.rules.sort(key=lambda r: -len(r[0]))

    def route(self, path: str) -> str | None:
        g, _ = self.route_and_key(path)
        return g

    def route_and_key(self, path: str) -> tuple[str | None, str]:
        """Group plus the path made relative to the matched prefix.

        Keys are stored relative because the absolute prefix is already implied
        by the group, and repeating it in every pointer was the single largest
        term in resident size (M7).
        """
        p = self._norm(path)
        for prefix, group in self.rules:
            if p == prefix:
                return group, ""
            if p.startswith(prefix + "/"):
                return group, p[len(prefix) + 1:]
        self.unrouted_count += 1
        if len(self.unrouted_samples) < 40:
            self.unrouted_samples.append(path)
        return None, p

    @classmethod
    def from_tree(cls, roots: dict[str, str], depth: int = 1) -> "Router":
        """Derive rules from the directory tree: each repo, plus its immediate
        subdirectories to `depth`.

        Kept for the experiment harness. Superseded by `from_index` for real use:
        a fixed depth makes groups out of whatever the directory layout happens
        to be, which produces regions like `sentinel/src` that cover an entire
        project and therefore say nothing when they light up.
        """
        r = cls()
        for root, label in roots.items():
            r.add(root, label)
            if depth >= 1 and os.path.isdir(root):
                try:
                    for entry in os.scandir(root):
                        if entry.is_dir() and not entry.name.startswith((".", "_")):
                            r.add(entry.path, f"{label}/{entry.name}")
                except OSError:
                    pass
        return r

    @classmethod
    def from_index(
        cls,
        roots: dict[str, str],
        list_files,
        max_files: int = 12,
        min_files: int = 3,
        max_depth: int = 5,
    ) -> "Router":
        """Derive routing from git's index, splitting until regions are useful.

        A region is only worth having if lighting up narrows the search. Fixed
        depth cannot do that: one project keeps everything in `src/`, another
        spreads across `android/app/src/main/...`, and the same depth gives a
        useless blob in the first and arbitrary slices of the second.

        So the tree is descended by *content* instead. A directory holding more
        than `max_files` tracked source files is too coarse and gets split into
        its children; one holding fewer than `min_files` is too thin to be its
        own region and folds back into its parent. What is left is a set of
        regions of comparable weight, whichever way a project is laid out.

        `list_files` supplies the tracked paths, so this costs one `git ls-files`
        per repo and never walks the filesystem.
        """
        r = cls()
        for root, label in roots.items():
            r.add(root, label)  # catch-all: the project itself
            files = [f for f in list_files(root) if f.strip()]
            if not files:
                continue
            for sub in _regions(files, max_files, min_files, max_depth):
                r.add(f"{root}/{sub}", f"{label}/{sub}")
        return r


class Field:
    def __init__(self, router: Router | None = None):
        self.groups: dict[str, Group] = {}
        self.router = router or Router()
        self.ingested = 0
        self.dropped = 0

    # -- ingest ------------------------------------------------------------

    def emit(
        self,
        path: str | None,
        channel: str,
        mass: float,
        now: float | None = None,
        group: str | None = None,
        key: str | None = None,
    ) -> str | None:
        """Absorb one signal. The only write path."""
        now = time.time() if now is None else now
        rel = None
        if group is None and path is not None:
            group, rel = self.router.route_and_key(path)
        if group is None:
            # Counted, never silently discarded: cold must mean "nothing
            # happened", not "nothing was routed".
            group = UNROUTED
            self.dropped += 1
        g = self.groups.get(group)
        if g is None:
            g = self.groups[group] = Group(group)
        g.channels[channel].add(now, mass, key=key or rel or path)
        self.ingested += 1
        return group

    def apply_state(
        self,
        channel: str,
        source: str,
        per_group: dict[str, tuple[float, dict[str, float]]],
    ) -> None:
        """Publish one sensor's complete standing picture, in one shot.

        Takes the *whole* answer rather than per-group updates, because the
        clearing half is the half that gets forgotten: a finding that was fixed,
        or a file that moved to another group, has to stop pressing on the old
        group, and it can only do that if something notices it is missing. So
        every group this field knows about is reset for this source, and only
        then are the reported ones set. Anything the sensor no longer mentions
        is retracted by omission.
        """
        for name, g in self.groups.items():
            if name in per_group:
                value, keys = per_group[name]
                g.channels[channel].set_level(source, value, keys)
            else:
                g.channels[channel].set_level(source, 0.0)
        # Groups seen by this sensor but not yet in the field at all.
        for name, (value, keys) in per_group.items():
            if name not in self.groups:
                g = self.groups[name] = Group(name)
                g.channels[channel].set_level(source, value, keys)

    # -- read --------------------------------------------------------------

    def magnitudes(self, now: float | None = None) -> dict[str, dict[str, float]]:
        now = time.time() if now is None else now
        return {
            name: {c: ch.magnitude(now) for c, ch in g.channels.items()}
            for name, g in self.groups.items()
        }

    def rank(self, now: float | None = None, channel: str = "R") -> list[tuple[str, float]]:
        now = time.time() if now is None else now
        out = [
            (name, g.channels[channel].magnitude(now))
            for name, g in self.groups.items()
            if name != UNROUTED
        ]
        out.sort(key=lambda kv: -kv[1])
        return out

    # -- persistence -------------------------------------------------------

    def save(self, path: str) -> int:
        # `collected_at` is the whole freshness story. The field is always
        # *internally* current -- decay is a function of time -- but it only
        # knows about signals that were absorbed. If the collector has not run
        # for three days, "cold" means "unobserved", not "quiet", and those are
        # opposite conclusions. Any reader must check this before trusting a
        # silence (2.0 sec.8.4).
        payload = {
            "v": 1,
            "collected_at": time.time(),
            "rings": list(RING_NAMES),
            "groups": {name: g.to_json() for name, g in self.groups.items()},
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, path)
        return os.path.getsize(path)

    @classmethod
    def load(cls, path: str, router: Router | None = None) -> "Field":
        f = cls(router)
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        for name, d in payload["groups"].items():
            f.groups[name] = Group.from_json(name, d)
        return f

    def state_bytes(self) -> int:
        """Resident cost: floats + timestamp + bounded key dict per channel."""
        n = 0
        for g in self.groups.values():
            for ch in g.channels.values():
                n += 8 * N_RINGS + 8 + 8 + 8  # rings, t, top_t, counter
                n += sum(len(k.encode()) + 8 for k in ch.top)
        return n
