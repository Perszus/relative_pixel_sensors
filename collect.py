"""Absorption pass. Cheap enough to run constantly, idempotent enough not to care.

Nothing here owns a process. This script is the parasite: it runs when something
else is already running it, does O(new signals) work, and exits. Between runs the
field is still current, because the state is a function of time (2.0 sec.3.1) --
"always on" does not mean "always running".

Usage:
    python collect.py            absorb, then print Layer 1
    python collect.py --quiet    absorb silently (exit 0)
    python collect.py --read        print Layer 1 without absorbing
    python collect.py --glance      print the orientation layer (cheap; for hooks)
    python collect.py --sensors     print the sensor spec sheet and exit
    python collect.py --rediscover  re-scan for repos, rewriting fleet.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rp import expectation, probes, rules, sensors, shape
from rp.sensors import REGISTRY, git
from rp.serve import brief, glance, layer1
from rp.store import Field, Router

HERE = os.path.dirname(os.path.abspath(__file__))
FIELD = os.path.join(HERE, "field.json")
VIEW = os.path.join(HERE, "view.json")
BRIEF = os.path.join(HERE, "BRIEF.txt")
MARKS = os.path.join(HERE, "watermarks.json")
SHAPES = os.path.join(HERE, "shapes.json")
FLEET_CFG = os.path.join(HERE, "fleet.json")

ROOTS_CFG = os.path.join(HERE, "roots.json")


def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _default_roots() -> tuple[str, ...]:
    """Where to look, in order of preference.

    A hardcoded root list is an atlas, and this is supposed to grow nerves
    wherever tissue is. So: an explicit override, then a config file, then the
    directory containing this tool -- which is almost always sitting alongside
    the things it should be watching.
    """
    env = os.environ.get("RP_ROOTS")
    if env:
        return tuple(p.strip().replace("\\", "/") for p in env.split(os.pathsep)
                     if p.strip())
    cfg = load_json(ROOTS_CFG, None)
    if cfg:
        roots = cfg.get("roots") if isinstance(cfg, dict) else cfg
        if roots:
            return tuple(str(p).replace("\\", "/") for p in roots)
    parent = os.path.dirname(HERE).replace("\\", "/")
    return (parent,)


FLEET_ROOTS = _default_roots()

# Directory names that are not what anyone calls the project. Local, because
# nobody else's repo is called what yours is.
ALIASES: dict[str, str] = load_json(os.path.join(HERE, "aliases.json"), {})


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
    # under another's name. It happens whenever an alias shortens one directory
    # to a name another directory already has.
    by_label: dict[str, list[str]] = {}
    for path, label in found.items():
        by_label.setdefault(label, []).append(path)
    for label, paths in by_label.items():
        if len(paths) > 1:
            for p in paths:
                found[p] = os.path.basename(p)
    return found


# Retained so older callers keep working; the live fleet comes from discovery
# or from fleet.json, both of which are local to whatever machine this runs on.
DEFAULT_FLEET: dict[str, str] = {}

BOOTSTRAP_WINDOW = "180 days ago"

# A commit that repairs something is a different statement from a commit that
# builds something. Both are real; only one is pressure.
FIX_WORDS = ("fix", "bug", "crash", "regress", "broke", "broken", "revert",
             "hotfix", "patch", "leak", "deadlock", "workaround")


@lru_cache(maxsize=64)
def _ls_files(repo: str) -> tuple[str, ...]:
    """Every tracked path, once per pass.

    Four callers wanted this list — routing, region sizing, the structural
    sensors and the shape graph — and three of them were spawning their own
    `git ls-files`. On eighteen repos that is thirty-six redundant process
    spawns, which on Windows is most of the cost of a pass.
    """
    return tuple(f for f in git(repo, "ls-files").splitlines() if f.strip())


def _tracked_source(repo: str) -> list[str]:
    """Tracked source files that are ours. Sizing routing by source alone keeps
    assets, generated output and vendored trees from inventing regions nobody
    would ever want to look at."""
    return [f for f in _ls_files(repo)
            if f.endswith(sensors.SOURCE_SUFFIX) and sensors.is_ours(f)]


def _tracked_all(repo: str) -> list[str]:
    return [f for f in _ls_files(repo) if sensors.is_ours(f)]


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


def _divergence(live: dict[str, str], forks: list) -> list[tuple[str, str, int, int, list]]:
    """Which files have actually drifted between near-identical projects.

    Filename overlap says two repositories are forks of one program, which is
    mildly interesting. Content hashing says *which* of their shared files no
    longer hold the same bytes, and those are the ones where a fix applied to
    one and not the other will hide. That is the actionable half, and it needs
    identity rather than names.
    """
    from rp import identity

    digests = {}
    for repo, label in live.items():
        if any(label in (a, b) for a, b, _ in forks):
            digests[label] = identity.source_digests(repo, _tracked_source(repo))

    out = []
    for a, b, _ in forks:
        if a not in digests or b not in digests:
            continue
        shared, drift, examples = identity.divergence(digests[a], digests[b])
        if drift:
            out.append((a, b, shared, drift, examples))
    out.sort(key=lambda t: -t[3])
    return out


def _forks(live: dict[str, str]) -> list[tuple[str, str, float]]:
    """Projects that are near-copies of each other.

    Compares the actual set of source filenames. A first attempt used language
    plus a similar source count, and paired two unrelated Python projects that
    happened to be a similar size. Shared filenames are what actually
    distinguishes a packaging fork from a coincidence.

    It matters wherever a codebase is forked per distribution target: every
    count in the brief then reports the same code more than once.
    """
    sigs: dict[str, set[str]] = {}
    for repo, label in live.items():
        names = {os.path.basename(f) for f in _tracked_source(repo)}
        if len(names) >= 8:
            sigs[label] = names
    out: list[tuple[str, str, float]] = []
    labels = sorted(sigs)
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            sa, sb = sigs[a], sigs[b]
            overlap = len(sa & sb) / min(len(sa), len(sb))
            if overlap >= 0.75:
                out.append((a, b, overlap))
    out.sort(key=lambda t: -t[2])
    return out


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
    return Router.from_index(live, _tracked_source)


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

def _probe_repo(repo: str, label: str, router, shape_cache: dict) -> dict:
    """Every standing/state sensor for one repo. Pure: touches no shared state,
    so repos can be probed in parallel -- which they are, because the cost here
    is process spawns rather than work."""
    all_files = list(_ls_files(repo))
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
        "secrets": sensors.secrets(repo, router),
        "doc_drift": sensors.doc_drift(repo, label, all_files),
        "suggestions": sensors.suggestions(repo, label),
        "shape": _shape_for(repo, label, router, all_files, shape_cache),
        "rules": _rules_for(repo, label, shape_cache),
        "kinds": sorted(rules.recognize(repo)),
        "meta": meta,
    }


def _rules_for(repo: str, label: str, cache: dict) -> list:
    """Rule findings, recomputed only when the repo has actually moved.

    Parsing every Python file to answer the grammar rules is 90% of a pass, and
    every one of those parses returns the same answer until something is
    committed. Keyed on HEAD, alongside shape.

    The trade is deliberate: a rule about a file edited but not yet committed
    will not fire until it is. These rules are statements about the repository,
    not about the working copy, and the sensors that *are* about the working
    copy -- `wip` and friends -- are not cached.
    """
    head = git(repo, "rev-parse", "HEAD").strip()
    hit = cache.get(label)
    if head and hit and hit.get("head") == head and "rules" in hit:
        return [rules.Finding(*row) for row in hit["rules"]]
    found = rules.evaluate(label, repo, rules.recognize(repo))
    if head:
        entry = cache.setdefault(label, {"head": head})
        entry["head"] = head
        entry["rules"] = [[f.rule, f.subject, f.kind, f.channel, f.value, f.says]
                          for f in found]
    return found


def _shape_for(repo: str, label: str, router, all_files: list[str],
               cache: dict) -> dict:
    """Structure, recomputed only when the repo has actually moved.

    Co-change walks 180 days of `git log --name-only` per repo, which took a
    steady-state pass from 3.6s to 10s — and every one of those walks returned
    an identical answer, because a dependency graph does not change when
    nothing is committed. Keyed on HEAD: if the sha is the same, so is the
    shape.
    """
    head = git(repo, "rev-parse", "HEAD").strip()
    hit = cache.get(label)
    if head and hit and hit.get("head") == head and "shape" in hit:
        return hit["shape"]
    result = shape.analyse(repo, label, router, all_files)
    if head:
        entry = cache.setdefault(label, {"head": head})
        if entry.get("head") != head:
            entry.clear()
            entry["head"] = head
        entry["shape"] = result
    return result


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
    "secrets": "R",
    "doc_drift": "R",
    "suggestions": "G",
}

# Project-scope health that every region inside the project inherits.
# `suggestions` deliberately does not: an idea offered about one part of a
# project does not vouch for the rest of it.
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
    shape_cache = load_json(SHAPES, {})

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
    fan_in: dict[str, int] = {}
    depends: dict[str, int] = {}
    coupled: list[tuple[int, str, str]] = []
    rule_findings: dict[tuple[str, str], dict] = {}
    # Sources whose findings arc at the spine. Collected here rather than looked
    # up, because machine reflexes are synthesised per volume — `disk-y-low`
    # exists only because a Y: drive does — and can never appear in the static
    # rule table.
    reflex_sources: set[str] = set()
    kinds_by_label: dict[str, list] = {}
    # The work is almost entirely waiting on git subprocesses, so the useful
    # width is the repo count, not the core count. Capped at 8 while the fleet
    # was 8 repos, which quietly halved throughput once it became 18.
    with ThreadPoolExecutor(max_workers=min(24, len(live) or 1)) as pool:
        futures = {pool.submit(_probe_repo, r, l, router, shape_cache): l
                   for r, l in live.items()}
        for fut, repo_label in futures.items():
            try:
                res = fut.result()
            except Exception:
                continue
            for name in CHANNEL:
                merged[name].update(res.get(name, {}))
            repo_meta = res.get("meta", {})
            kinds_by_label[repo_label] = res.get("kinds", [])
            sh = res.get("shape") or {}
            fan_in.update(sh.get("fan_in", {}))
            depends.update(sh.get("depends", {}))
            # `repo_label` comes from the futures map, not from an enclosing
            # loop. Reading `label` here picked up whatever the *events* loop
            # had left bound, so every coupled pair in the fleet was attributed
            # to one arbitrary project — a JNI pair belonging to one repo was
            # reported under a different repo's name.
            for n, a, b in sh.get("coupled", []):
                coupled.append((n, f"{repo_label}/{a}", f"{repo_label}/{b}"))
            for m in repo_meta.values():
                m["entries"] = sh.get("entries", [])
                m["kinds"] = res.get("kinds", [])
            meta.update(repo_meta)

            # One source per rule, so a rule can be wrong without being
            # anonymous. At this volume nobody audits rules individually; what
            # makes that survivable is that the field can always say which one
            # spoke and about what.
            for f in res.get("rules", []):
                rule_findings.setdefault((f.channel, f.rule), {})[f.subject] = \
                    (f.value, {f.says: f.value})
                if f.reflex:
                    reflex_sources.add(f"rule:{f.rule}")

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

    # Peer-derived expectations. The fleet sets its own norms: if most projects
    # of a kind have a thing and one does not, that is a gap the system itself
    # defined, not an opinion anyone encoded. It needs every subject in hand at
    # once, so it cannot be a per-subject probe.
    peers = {label: (repo, set(kinds_by_label.get(label, ())))
             for repo, label in live.items()}
    for label, missing in expectation.peer_gaps(peers).items():
        if missing:
            rule_findings.setdefault(("R", "peer-gap"), {})[label] = (
                min(1.2 * len(missing), 8.0),
                {m: 1.0 for m in missing[:4]})

    # The machine itself is a subject. "The system drive is full" is not a
    # property of any project -- attaching it to eighteen of them would report
    # one fact eighteen times -- so it gets a region of its own.
    for f in rules.machine():
        rule_findings.setdefault((f.channel, f.rule), {})[f.subject] = \
            (f.value, {f.says: f.value})
        if f.reflex:
            reflex_sources.add(f"rule:{f.rule}")

    # Rule sources are namespaced so they cannot collide with the hand-written
    # sensors, and so `sources` in the view reads as a rule id.
    for (channel, rule_id), per_subject in rule_findings.items():
        field.apply_state(channel, f"rule:{rule_id}", per_subject)
    # Retract rules that fired last pass and found nothing this one.
    for rule in (*rules.RULES, *rules.MACHINE_RULES):
        key = (rule.chan, rule.id)
        if rule.chan and key not in rule_findings:
            field.apply_state(rule.chan, f"rule:{rule.id}", {})

    # Normalise meta for the readers: they want "20d ago", not an epoch.
    meta_view = {
        name: {**m, "last_commit": sensors.age_desc(m.get("last_commit"))}
        for name, m in meta.items()
    }

    sizes = region_sizes(live, router)
    coupled.sort(reverse=True)
    forks = _forks(live)
    shapes = {"fan_in": fan_in, "depends": depends, "coupled": coupled[:5],
              "forks": forks, "divergence": _divergence(live, forks),
              "reflex_sources": sorted(reflex_sources)}

    size = field.save(FIELD)
    write_view(field, VIEW, meta, sizes, live, shapes)
    with open(BRIEF, "w", encoding="utf-8") as fh:
        fh.write(brief(field, meta_view, sizes, shapes) + "\n")
    with open(MARKS, "w", encoding="utf-8") as fh:
        json.dump(marks, fh, indent=2)
    with open(SHAPES, "w", encoding="utf-8") as fh:
        json.dump(shape_cache, fh, separators=(",", ":"))
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


def _still_there(region: str, key: str, live: dict[str, str]) -> bool:
    """Does this pointer still name a file that exists?

    Keys accumulate and decay; files get renamed and deleted, and nothing tells
    the field when that happens. A stale key stays weighted for weeks.
    """
    key = re.sub(r"\s*\(\d+\s*[KM]B\)$", "", key)
    label = region.split("/", 1)[0]
    root = next((p for p, l in live.items() if l == label), None)
    if root is None:
        return True  # cannot check; do not silently hide it
    prefix = region.split("/", 1)[1] if "/" in region else ""
    return os.path.isfile(os.path.join(root, prefix, key) if prefix
                          else os.path.join(root, key))


@lru_cache(maxsize=1)
def _descriptions() -> dict:
    """Every finding id to the words it should be read by.

    Rules carry `says`; the hand-written sensors carry `doc`. Both are
    written for a reader, which is the point -- see `_describe`.
    """
    out = {r.id: r.says for r in rules.RULES}
    for spec in REGISTRY:
        out.setdefault(spec.name, spec.doc)
    return out


def _describe(source: str, channel) -> str:
    """The words a finding should be read by.

    The rule's own description wins over the level key. Level keys are
    per-subject -- usually a filename -- and a filename is not a finding: a
    reader shown `calendar_database_service.dart` still has to go and discover
    what is wrong with it, which is exactly the expensive step this layer
    exists to skip. The key is appended as detail, never used alone.
    """
    rule_id = source.replace("rule:", "")
    said = _descriptions().get(rule_id)
    key = next(iter(channel.level_keys.get(source, {})), "")
    if said:
        # Sensor docs are two sentences: the finding, then commentary.
        said = said.rstrip(".").split(". ")[0]
        # The key is worth appending only when it names a *subject* the
        # description does not -- a file, usually. For rule findings the key
        # is the description itself, and repeating it reads as a stutter.
        subject = key if key and key != said and ("/" in key or "." in key) else ""
        return f"{said} ({subject})" if subject else said
    return f"{rule_id}: {key}" if key else rule_id


def write_view(field: Field, path: str, meta: dict, sizes: dict | None = None,
               live: dict[str, str] | None = None,
               shapes: dict | None = None) -> None:
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
        # Pointers name files to go and look at, so one naming a file that no
        # longer exists is worse than none: the top pointer for one Android project's java
        # region outranked the live file while pointing at a package that had
        # been renamed away. Historically true, and useless as an instruction.
        #
        # Standing keys come first when standing dominates. A region whose
        # pressure is entirely unresolved findings was pointing at decayed
        # activity keys instead -- three filenames with weight 0.0, when the
        # three files that actually carry the findings were sitting right there.
        candidates: list[tuple[str, float]] = []
        if r.level > 0.0 and r.level >= r.read(now)[1]:
            candidates = sorted(r.standing_keys().items(), key=lambda kv: -kv[1])
        candidates += r.pointers(now, 8) or b.pointers(now, 8)
        seen: set[str] = set()
        pointers = []
        for key, weight in candidates:
            if key in seen or not _still_there(name, key, live):
                continue
            seen.add(key)
            pointers.append((key, weight))
            if len(pointers) == 3:
                break
        entry = {
            "name": name,
            "rgb": [round(r.magnitude(now), 3), round(gr.magnitude(now), 3),
                    round(b.magnitude(now), 3)],
            "profile": r.effective_profile(now),
            "standing": round(r.level, 2),
            "sources": standing,
            "concentration": round(r.concentration(now), 3),
            "pointers": [[k, round(v, 2)] for k, v in pointers],
            "size": (sizes or {}).get(name, 0),
            # Structure, not condition. How many other regions import from this
            # one, and how many it imports from.
            "fan_in": (shapes or {}).get("fan_in", {}).get(name, 0),
            "depends": (shapes or {}).get("depends", {}).get(name, 0),
            # Nociceptor findings, in the words they carry. Kept separate from
            # `sources` so a viewer cannot accidentally render an emergency as
            # one more weighted row.
            "reflexes": [
                next(iter(r.level_keys.get(s, {})), s.replace("rule:", ""))
                for s in sorted(r.standing_by_source())
                if s in set((shapes or {}).get("reflex_sources", ()))
            ],
            # Findings no ordinary tool would have surfaced, in the words they
            # carry. A magnitude says a region is loud; only the words say
            # what it is, and "loud" is not oversight -- a reader still has to
            # go and look. These are what the orientation layer leads with, so
            # the expensive question is answered before it is asked.
            "notable": [
                [
                    s.replace("rule:", ""),
                    _describe(s, r),
                    round(r.standing_by_source().get(s, 0.0), 2),
                ]
                for s in sorted(r.standing_by_source())
                if rules.noteworthy(s.replace("rule:", ""))
            ],
        }
        if name in meta:
            m = meta[name]
            entry["meta"] = {
                "files": m.get("files"),
                "source": m.get("source"),
                "tests": m.get("tests"),
                "kind": m.get("kind"),
                "reviewed": m.get("reviewed"),
                "entries": m.get("entries", []),
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
    if "--glance" in args:
        # Runs unattended at session start, so it must never be the reason a
        # session fails to open: every path here exits 0, and a field that
        # cannot be read says so in one line instead of raising.
        try:
            with open(VIEW, encoding="utf-8") as fh:
                print(glance(json.load(fh), HERE))
        except FileNotFoundError:
            print(f"relativity pixels: no field yet — run `python collect.py` in {HERE}")
        except Exception as exc:  # noqa: BLE001 - see above
            print(f"relativity pixels: field unreadable ({exc.__class__.__name__}: {exc})")
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
