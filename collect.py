"""Absorption pass. Cheap enough to run constantly, idempotent enough not to care.

Nothing here owns a process. This script is the parasite: it runs when something
else is already running it, does O(new signals) work, and exits. Between runs the
field is still current, because the state is a function of time (2.0 sec.3.1) --
"always on" does not mean "always running".

Usage:
    python collect.py            absorb, then print Layer 1
    python collect.py --quiet    absorb silently (exit 0)
    python collect.py --read        print Layer 1 without absorbing
    python collect.py --sensors     print the sensor spec sheet and exit
    python collect.py --rediscover  re-scan for repos, rewriting fleet.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rp import sensors
from rp.sensors import REGISTRY, git
from rp.serve import brief, layer1
from rp.store import Field, Router

HERE = os.path.dirname(os.path.abspath(__file__))
FIELD = os.path.join(HERE, "field.json")
VIEW = os.path.join(HERE, "view.json")
BRIEF = os.path.join(HERE, "BRIEF.txt")
MARKS = os.path.join(HERE, "watermarks.json")
FLEET_CFG = os.path.join(HERE, "fleet.json")

# Where repos live. Discovery beats a hand-written list: the first version of
# this file named eight repos and the machine had eighteen, so the field was
# blind to more than half the fleet -- including its own repo, a site in
# production, and every packaging target. A map that silently covers 44% of the
# thing it claims to map is worse than one that admits its edges.
#
# Arch is deliberately not here. It is the archive tier, so everything in it is
# dormant by definition and would fire the "untouched for months" pattern
# forever -- a signal that is always on is off. Add it to this tuple to watch it.
FLEET_ROOTS = ("F:/Code/Development", "F:/Code/Production")

# Friendlier names for repos whose directory is not what anyone calls them.
ALIASES = {
    "ouroborous_android": "orobos",
    "ester_code_slim": "ester",
    "this_note_windows": "thisnote",
    "purite_windows": "purity",
    "huthut_windows": "huts",
    "relative_pixel_sensors": "pixels",
    "bolwarra-technologies-website": "bolwarra",
}


def discover_fleet() -> dict[str, str]:
    """Every git repo under the known roots, keyed by path.

    Skips repos with no commits: an empty repo has nothing to say and would
    only add a permanently cold region. Everything else is included, because
    deciding in advance which projects are worth watching is precisely the
    judgement this tool exists to replace.
    """
    found: dict[str, str] = {}
    for root in FLEET_ROOTS:
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir() or not os.path.isdir(os.path.join(entry.path, ".git")):
                continue
            path = entry.path.replace("\\", "/")
            if not git(path, "rev-parse", "HEAD").strip():
                continue
            found[path] = ALIASES.get(entry.name, entry.name)

    # Two repos sharing a label would silently merge into one set of regions,
    # and the merge is invisible: the field just reports one project's pressure
    # under another's name. `ester_code_slim` is aliased to "ester" and there is
    # also a directory literally called "ester".
    by_label: dict[str, list[str]] = {}
    for path, label in found.items():
        by_label.setdefault(label, []).append(path)
    for label, paths in by_label.items():
        if len(paths) > 1:
            for p in paths:
                found[p] = os.path.basename(p)
    return found


# Retained so the harness and older callers keep working; the live fleet comes
# from discovery or from fleet.json.
DEFAULT_FLEET = {
    "F:/Code/Development/veil": "veil",
    "F:/Code/Development/ouroborous_android": "orobos",
    "F:/Code/Development/ester_code_slim": "ester",
    "F:/Code/Development/sentinel": "sentinel",
    "F:/Code/Development/paranoia": "paranoia",
    "F:/Code/Production/this_note_windows": "thisnote",
    "F:/Code/Production/purite_windows": "purity",
    "F:/Code/Production/huthut_windows": "huts",
}

BOOTSTRAP_WINDOW = "180 days ago"

# A commit that repairs something is a different statement from a commit that
# builds something. Both are real; only one is pressure.
FIX_WORDS = ("fix", "bug", "crash", "regress", "broke", "broken", "revert",
             "hotfix", "patch", "leak", "deadlock", "workaround")


def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


@lru_cache(maxsize=64)
def _tracked_source(repo: str) -> tuple[str, ...]:
    """Tracked source files, from git's index. Sizing routing by source alone
    keeps assets, generated output and vendored trees from inventing regions
    nobody would ever want to look at.

    Cached because routing and region sizing both want it, and one `git
    ls-files` per repo per pass is the whole budget for knowing the shape of
    the fleet.
    """
    return tuple(
        f for f in git(repo, "ls-files").splitlines()
        if f.strip().endswith(sensors.SOURCE_SUFFIX) and sensors.is_ours(f)
    )


@lru_cache(maxsize=64)
def _tracked_all(repo: str) -> tuple[str, ...]:
    return tuple(f for f in git(repo, "ls-files").splitlines()
                 if f.strip() and sensors.is_ours(f))


def region_sizes(live: dict[str, str], router: Router) -> dict[str, int]:
    """Files per region — the denominator.

    Without it every reading is partly a measure of how big a place is, and the
    same few large regions win the ranking permanently regardless of their
    condition (2.0 sec.4.3 Q4). Kept as a separate number rather than divided
    in, because absolute and relative answer different questions: ten findings
    cost ten findings' worth of work wherever they are, but ten findings in a
    four-file region is a different *kind* of news.

    Counts every tracked file, not just source. Routing is *sized* by source --
    that decides where the boundaries go -- but pressure arrives on
    non-source files too (an oversized binary, a committed asset), and dividing
    those by a source-only count produced densities like 31.8 for a region
    holding one source file and two stray binaries.
    """
    sizes: dict[str, int] = {}
    for repo in live:
        for f in _tracked_all(repo):
            g, _ = router.route_and_key(f"{repo}/{f}")
            if g:
                sizes[g] = sizes.get(g, 0) + 1
    return sizes


def load_fleet(rediscover: bool = False) -> dict[str, str]:
    """The watched fleet: whatever fleet.json says, or discovery if it is absent.

    An edited fleet.json wins, because pruning a repo you deliberately do not
    want watched should stick. `--rediscover` is the way back to the full set.
    """
    if not rediscover:
        existing = load_json(FLEET_CFG, None)
        if existing:
            return existing
    fleet = discover_fleet()
    with open(FLEET_CFG, "w", encoding="utf-8") as fh:
        json.dump(fleet, fh, indent=2)
    return fleet


def build_router(fleet: dict[str, str]) -> Router:
    live = {k: v for k, v in fleet.items() if os.path.isdir(k)}
    return Router.from_index(live, lambda r: list(_tracked_source(r)))


# ---------------------------------------------------------------- event sensor

def ingest_git(field: Field, repo: str, label: str, marks: dict) -> tuple[int, str | None]:
    """Commits since the watermark, at their real timestamps.

    The only sensor that writes events rather than standing statements, and the
    only one that needs a watermark: everything else re-derives its whole answer
    each pass, which is what lets it clear. Events cannot be re-derived without
    double-counting, so this one remembers where it got to.

    A post-commit hook would be tighter, but hooks are dead on this machine
    (sh.exe 0xC0000142), so the watermark is how it stays exactly-once instead.
    """
    head = git(repo, "rev-parse", "HEAD").strip()
    if not head:
        return 0, None
    last = marks.get(label)
    if last == head:
        return 0, head
    rng = [f"{last}..HEAD"] if last else [f"--since={BOOTSTRAP_WINDOW}"]

    log = git(repo, "log", *rng, "--pretty=format:@%ct%x00%s", "--name-only")
    n = 0
    ts = time.time()
    is_fix = False
    for line in log.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("@"):
            stamp, _, subject = line[1:].partition("\x00")
            try:
                ts = float(stamp)
            except ValueError:
                ts = time.time()
            low = subject.lower()
            is_fix = any(w in low for w in FIX_WORDS)
            continue
        field.emit(f"{repo}/{line}", "B", sensors.W_ACTIVITY, now=ts)
        # Activity counts everywhere -- bumping a vendored library is real work
        # and worth seeing. Pressure does not: a repair inside third-party code
        # is not a thing anyone here is going to be asked to fix.
        if is_fix and sensors.is_ours(line):
            field.emit(f"{repo}/{line}", "R", sensors.W_FIX, now=ts)
        n += 1
    return n, head


# ---------------------------------------------------------------- the pass

def _probe_repo(repo: str, label: str, router) -> dict:
    """Every standing/state sensor for one repo. Pure: touches no shared state,
    so repos can be probed in parallel -- which they are, because the cost here
    is process spawns rather than work."""
    all_files = [f for f in git(repo, "ls-files").splitlines() if f.strip()]
    last = git(repo, "log", "-1", "--format=%ct").strip()
    last_commit = float(last) if last else None

    findings, clean = sensors.ester(repo, router)
    tested, documented, meta = sensors.structure(repo, label, all_files, last_commit)
    failing, note = sensors.tests_failing(repo, router, last_commit)
    if note and label in meta:
        meta[label].update(note)
    return {
        "wip": sensors.wip(repo, router),
        "todo": sensors.todo(repo, router),
        "ester_open": findings,
        "ester_clean": clean,
        "unpushed": sensors.unpushed(repo, label),
        "tested": tested,
        "documented": documented,
        "tests_failing": failing,
        "ci": sensors.ci(repo, label, all_files),
        "lock_drift": sensors.lock_drift(repo, label, all_files),
        "heavy_files": sensors.heavy_files(repo, router, all_files),
        "conflicts": sensors.conflicts(repo, router),
        "giants": sensors.giants(repo, router, all_files),
        "meta": meta,
    }


# Which channel each standing/state sensor writes to.
CHANNEL = {
    "wip": "B",
    "todo": "R",
    "ester_open": "R",
    "ester_clean": "G",
    "unpushed": "R",
    "tested": "G",
    "documented": "G",
    "tests_failing": "R",
    "ci": "G",
    "lock_drift": "R",
    "heavy_files": "R",
    "conflicts": "R",
    "giants": "R",
}

# Project-scope health that every region inside the project inherits.
PROPAGATES = ("tested", "documented", "ci")


def _inherit(per_group: dict, all_groups: set[str]) -> dict:
    """Push a project's value down to its regions."""
    out = dict(per_group)
    for project, value in per_group.items():
        for g in all_groups:
            if g.startswith(project + "/"):
                out[g] = value
    return out


def collect(quiet: bool = False) -> Field:
    fleet = load_fleet()
    live = {r: l for r, l in fleet.items() if os.path.isdir(r)}

    router = build_router(live)
    field = Field.load(FIELD, router) if os.path.isfile(FIELD) else Field(router)
    marks = load_json(MARKS, {})

    t0 = time.perf_counter()

    # Events first, serially: they mutate the field.
    commits = 0
    for repo, label in live.items():
        n, head = ingest_git(field, repo, label, marks)
        commits += n
        if head:
            marks[label] = head

    # Everything else in parallel. Each sensor's answer must be complete across
    # the whole fleet before it is applied, because apply_state retracts every
    # group the sensor did not mention -- a per-repo apply would clear the
    # groups belonging to every other repo.
    merged: dict[str, dict] = {k: {} for k in CHANNEL}
    meta: dict[str, dict] = {}
    # The work is almost entirely waiting on git subprocesses, so the useful
    # width is the repo count, not the core count. Capped at 8 while the fleet
    # was 8 repos, which quietly halved throughput once it became 18.
    with ThreadPoolExecutor(max_workers=min(24, len(live) or 1)) as pool:
        futures = {pool.submit(_probe_repo, r, l, router): l for r, l in live.items()}
        for fut in futures:
            try:
                res = fut.result()
            except Exception:
                continue
            for name in CHANNEL:
                merged[name].update(res.get(name, {}))
            meta.update(res.get("meta", {}))

    # Project-scope health is true of every region inside the project: if the
    # repo has CI, CI covers this directory too. Without propagating it, 34 of
    # 45 regions read as "nothing has vouched for them" -- which was a gap in
    # how the sensor was scoped, not a fact about the code.
    #
    # Only health propagates. "This project has unpushed commits" is not a
    # property of any particular region, and pushing project-scope *pressure*
    # down would just paint every region red for one repo-level fact.
    all_groups = {g for _, g in router.rules}
    for name in PROPAGATES:
        merged[name] = _inherit(merged[name], all_groups)

    for name, per_group in merged.items():
        field.apply_state(CHANNEL[name], name, per_group)

    # Normalise meta for the readers: they want "20d ago", not an epoch.
    meta_view = {
        name: {**m, "last_commit": sensors.age_desc(m.get("last_commit"))}
        for name, m in meta.items()
    }

    sizes = region_sizes(live, router)

    size = field.save(FIELD)
    write_view(field, VIEW, meta, sizes)
    with open(BRIEF, "w", encoding="utf-8") as fh:
        fh.write(brief(field, meta_view, sizes) + "\n")
    with open(MARKS, "w", encoding="utf-8") as fh:
        json.dump(marks, fh, indent=2)
    el = time.perf_counter() - t0

    if not quiet:
        counts = " \u00b7 ".join(
            f"{n} {name}" for name, n in
            ((k, sum(len(v[1]) or 1 for v in merged[k].values())) for k in CHANNEL)
            if n
        )
        print(f"absorbed {commits} file-touches \u00b7 {counts} "
              f"in {el*1000:.0f} ms  (field {size/1024:.0f} KB)")
    return field


def write_view(field: Field, path: str, meta: dict, sizes: dict | None = None) -> None:
    """Render the field into exactly what a viewer needs, and nothing more.

    Sentinel reads this instead of field.json on purpose. Every rule about what
    the field *means* -- decay, ring weighting, what counts as warming, when a
    judgment is standing -- stays in one implementation. A second one in the
    renderer would eventually disagree with this one, and a heatmap that quietly
    contradicts its own store is worse than no heatmap.
    """
    now = time.time()
    groups = []
    for name, g in sorted(field.groups.items()):
        if name.startswith("__"):
            continue
        r, gr, b = (g.channels[c] for c in ("R", "G", "B"))
        standing = {}
        for ch in (r, gr, b):
            for src, v in ch.standing_by_source().items():
                standing[src] = round(v, 2)
        pointers = r.pointers(now, 3) or b.pointers(now, 3)
        if not pointers:
            keys = r.standing_keys() or b.standing_keys()
            pointers = sorted(keys.items(), key=lambda kv: -kv[1])[:3]
        entry = {
            "name": name,
            "rgb": [round(r.magnitude(now), 3), round(gr.magnitude(now), 3),
                    round(b.magnitude(now), 3)],
            "profile": r.profile(now),
            "standing": round(r.level, 2),
            "sources": standing,
            "concentration": round(r.concentration(now), 3),
            "pointers": [[k, round(v, 2)] for k, v in pointers],
            "size": (sizes or {}).get(name, 0),
        }
        if name in meta:
            m = meta[name]
            entry["meta"] = {
                "files": m.get("files"),
                "source": m.get("source"),
                "tests": m.get("tests"),
                "kind": m.get("kind"),
                "reviewed": m.get("reviewed"),
                "last_commit": sensors.age_desc(m.get("last_commit")),
            }
        groups.append(entry)
    groups.sort(key=lambda d: -d["rgb"][0])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"collected_at": now, "groups": groups}, fh, separators=(",", ":"))
    os.replace(tmp, path)


def print_sensors() -> None:
    print(f"{'sensor':<14} {'ch':<3} {'kind':<9} {'scope':<8} clears")
    print("-" * 78)
    for s in REGISTRY:
        print(f"{s.name:<14} {s.channel:<3} {s.kind:<9} {s.scope:<8} {s.clears}")
        print(f"{'':>14} {s.doc}")


def main() -> int:
    args = set(sys.argv[1:])
    if "--sensors" in args:
        print_sensors()
        return 0
    if "--read" in args:
        field = Field.load(FIELD, build_router(load_fleet()))
        print(layer1(field))
        return 0
    if "--rediscover" in args:
        fleet = load_fleet(rediscover=True)
        print(f"discovered {len(fleet)} repos:")
        for path, label in sorted(fleet.items(), key=lambda kv: kv[1]):
            print(f"  {label:<16} {path}")
    field = collect(quiet="--quiet" in args)
    if "--quiet" not in args:
        print(layer1(field))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
