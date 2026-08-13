"""REMOTE probes — something off this machine.

Split so the *probe* never touches the network. Refreshing is an explicit
command run occasionally; the probe reads the snapshot that refresh left
behind, which makes it a local file read like any other.

That split is the whole design. A probe that fetches makes the field's
availability depend on somebody else's, and a fetch that times out returns
nothing — which reads as clean. Here a missing or expired snapshot reports
UNKNOWN, because **"could not check" is not a pass**, and that distinction is
the only thing that makes a security-shaped signal safe to publish.

    python -m rp.remote --refresh     fetch advisories for declared deps
    python -m rp.remote --status      what the snapshot knows and how old it is
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

DAY = 86400.0
SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "advisories.json")

# Past this the snapshot is not evidence. Advisories are published continuously,
# so an old one cannot distinguish "nothing known" from "nothing fetched".
SNAPSHOT_TTL_DAYS = 14.0

OSV_API = "https://api.osv.dev/v1/querybatch"


def _requirements(root: str) -> dict[str, str]:
    """Declared Python dependencies, name -> pinned version (or "")."""
    out: dict[str, str] = {}
    for name in ("requirements.txt", "requirements-dev.txt"):
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.split("#", 1)[0].strip()
                    if not line or line.startswith("-"):
                        continue
                    m = re.match(r"^([A-Za-z0-9._-]+)\s*(?:==\s*([\w.]+))?", line)
                    if m:
                        out[m.group(1).lower()] = m.group(2) or ""
        except OSError:
            continue
    return out


def load() -> dict:
    try:
        with open(SNAPSHOT, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def snapshot_age_days() -> float:
    data = load()
    at = data.get("at", 0)
    return (time.time() - at) / DAY if at else float("inf")


def usable() -> bool:
    """Is there evidence at all, and is it recent enough to mean anything?"""
    return bool(load().get("packages")) and snapshot_age_days() <= SNAPSHOT_TTL_DAYS


def advisories_for(root: str) -> tuple[int, tuple[str, ...]]:
    """(count, package names) with known advisories among this project's
    declared dependencies.

    Callers must check `usable()` first. This returns zero for an absent
    snapshot as well as for a clean one, and those are opposite facts — the
    probe layer is where that distinction is enforced.
    """
    known = load().get("packages", {})
    hits = [name for name in _requirements(root) if known.get(name)]
    return len(hits), tuple(sorted(hits))


# ------------------------------------------------------------------ refresh


def refresh(roots: list[str], timeout: int = 30) -> dict:
    """Fetch advisories for every declared dependency across the given roots.

    Explicit, occasional, and out of band. Never called during a pass.
    """
    wanted: set[str] = set()
    for root in roots:
        wanted |= set(_requirements(root))
    if not wanted:
        return {"at": time.time(), "packages": {}, "queried": 0}

    packages: dict[str, int] = {}
    names = sorted(wanted)
    for chunk_start in range(0, len(names), 100):
        chunk = names[chunk_start:chunk_start + 100]
        body = json.dumps({"queries": [
            {"package": {"name": n, "ecosystem": "PyPI"}} for n in chunk]})
        req = urllib.request.Request(
            OSV_API, data=body.encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
            # Partial results are still results, but the failure is recorded so
            # a half-fetched snapshot cannot pass as a complete one.
            return {"at": time.time(), "packages": packages,
                    "queried": len(packages), "incomplete": str(e)}
        for name, result in zip(chunk, data.get("results", [])):
            vulns = result.get("vulns") or []
            if vulns:
                packages[name] = len(vulns)

    return {"at": time.time(), "packages": packages, "queried": len(names)}


def _main(argv: list[str]) -> int:
    if "--status" in argv:
        data = load()
        age = snapshot_age_days()
        if not data:
            print("no snapshot — run `python -m rp.remote --refresh`")
            return 1
        print(f"snapshot: {data.get('queried', 0)} packages queried, "
              f"{len(data.get('packages', {}))} with advisories")
        print(f"age: {age:.1f} days "
              f"({'usable' if usable() else 'EXPIRED — reports UNKNOWN'})")
        if data.get("incomplete"):
            print(f"incomplete: {data['incomplete']}")
        return 0

    if "--refresh" in argv:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from collect import load_fleet
        roots = [r for r in load_fleet() if os.path.isdir(r)]
        print(f"querying advisories for dependencies across {len(roots)} projects…")
        data = refresh(roots)
        with open(SNAPSHOT, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        print(f"{data.get('queried', 0)} packages queried, "
              f"{len(data.get('packages', {}))} with advisories")
        if data.get("incomplete"):
            print(f"INCOMPLETE: {data['incomplete']}")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
