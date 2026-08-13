"""Serving: Layer 1 (magnitude + direction, no nouns) and Layer 2 (enough to
decide where to look). Consumer-agnostic on purpose -- both layers come out of
a plain query and contain no formatting for any particular reader.
"""

from __future__ import annotations

import time

from .store import CHANNELS, UNROUTED, Field

# Coarse buckets. Layer 1 must not imply precision it does not have.
#
# Log-spaced, because the measured distribution is heavy-tailed. Deliberately
# ABSOLUTE rather than quantile-based: quantiles would guarantee an even spread
# of symbols and so would paint a quiet fleet as though something were
# happening. A quiet field must be allowed to look quiet.
#
# PER CHANNEL, because the three do not live on the same scale and cannot be
# made to. Measured fleet totals: activity 1036, pressure 181, health 35. That
# is not a calibration error to be tuned away -- one commit touches thirty files
# while one finding is one finding, so activity is intrinsically an order of
# magnitude larger. Forcing the weights to match would mean falsifying what each
# sensor measures in order to make a renderer's life easier. Instead each
# channel is banded against its own distribution, so `#..` means "high pressure,
# low health, no activity" in each channel's own terms -- which is exactly what
# the four-quadrant reading needs.
_BANDS = {
    "R": ((1.0, "."), (4.0, "-"), (12.0, "+"), (float("inf"), "#")),
    "G": ((1.0, "."), (3.0, "-"), (7.0, "+"), (float("inf"), "#")),
    "B": ((4.0, "."), (20.0, "-"), (80.0, "+"), (float("inf"), "#")),
}


def _band(v: float, channel: str = "R") -> str:
    for edge, sym in _BANDS[channel]:
        if v < edge:
            return sym
    return "#"


_ARROW = {
    "warming": "^",
    "spike": "!",
    "sustained": "=",
    "cooling": "v",
    "standing": "*",
    "cold": " ",
}


def _mark(ch, now: float) -> str:
    """Direction symbol, with standing judgments overriding the temporal read.

    `profile()` describes activity, and activity is the wrong axis when the
    reason a region is loud is that something is unresolved there. A region with
    open findings and no recent commits is temporally 'cooling' and practically
    abandoned -- reporting the former hides the latter, which is the single
    misread this whole layer exists to prevent (2.0 sec.7, red-without-blue).
    """
    if ch.level > 0.0 and ch.level >= ch.read(now)[1]:
        return _ARROW["standing"]
    return _ARROW[ch.profile(now)]


def layer1(field: Field, now: float | None = None, limit: int = 12) -> str:
    """Pushed unconditionally. Magnitude and direction only -- deliberately
    says nothing about *what*, because the what is the next step and it is the
    expensive one."""
    now = time.time() if now is None else now
    rows = []
    for name, g in field.groups.items():
        if name == UNROUTED:
            continue
        r = g.channels["R"]
        m = r.magnitude(now)
        if m <= 0.0:
            continue
        rows.append((m, name, _band(m, "R"), _mark(r, now)))
    rows.sort(key=lambda t: -t[0])
    if not rows:
        return "field: all cold"
    out = " ".join(f"{name}{sym}{arrow}".rstrip() for _, name, sym, arrow in rows[:limit])
    quiet = sum(1 for _, g in field.groups.items() if g.channels["R"].magnitude(now) <= 0.0)
    return f"field: {out}  ({quiet} cold)"


def layer2(field: Field, group: str, now: float | None = None) -> dict:
    """Pulled. Channel breakdown, temporal profile, concentration, pointers.

    Exists as a safety requirement, not a convenience: a bare magnitude invites
    over-trust, and this is the layer that lets a reader reject "loud but
    trivial" before spending anything on it.
    """
    now = time.time() if now is None else now
    g = field.groups.get(group)
    if g is None:
        return {"group": group, "state": "unknown"}
    out: dict = {"group": group, "channels": {}}
    for c in CHANNELS:
        ch = g.channels[c]
        mag = ch.magnitude(now)
        if mag <= 0.0 and not ch.top:
            continue
        entry = {
            "magnitude": round(mag, 3),
            "profile": ch.profile(now),
            "concentration": round(ch.concentration(now), 3),
            "pointers": [(k, round(v, 2)) for k, v in ch.pointers(now)],
            "events": ch.total_events,
        }
        if ch.level:
            # Split out, because "loud because it is being worked on" and "loud
            # because something is unresolved" are opposite recommendations.
            entry["standing"] = round(ch.level, 2)
            entry["standing_at"] = sorted(ch.level_keys, key=ch.level_keys.__getitem__,
                                          reverse=True)[:3]
        out["channels"][c] = entry
    if not out["channels"]:
        out["state"] = "cold"
    return out


def brief(field: Field, meta: dict, sizes: dict | None = None,
          now: float | None = None) -> str:
    """The telescope: the whole fleet at low magnification, on one screen.

    Everything else in this module answers a question about one place. This
    answers "where should I point", which is a different instrument. It is
    organised by what a reader can *do* about each section rather than by which
    sensor produced it -- pressure nobody is working on is a different call from
    pressure someone is already on, and the sensor that noticed is not the point.

    Cold regions are listed by name rather than omitted. Silence is the larger
    half of the payload (2.0 sec.8.4): knowing that twenty-five regions have
    nothing to say is what licenses not looking at them, and a section that
    quietly disappears licenses nothing.
    """
    now = time.time() if now is None else now
    sizes = sizes or {}
    rows = []
    for name, g in field.groups.items():
        if name == UNROUTED:
            continue
        r, gr, b = (g.channels[c] for c in CHANNELS)
        n = sizes.get(name, 0)
        pressure = r.magnitude(now)
        rows.append({
            "name": name,
            "r": pressure, "g": gr.magnitude(now), "b": b.magnitude(now),
            "size": n,
            # Pressure per source file. The same ten findings mean something
            # different in a four-file region than in a fifty-file one.
            "density": (pressure / n) if n else 0.0,
            "profile": r.profile(now),
            "sources": {**r.standing_by_source(), **gr.standing_by_source(),
                        **b.standing_by_source()},
            "why": _why(r, gr, b),
        })

    # A telescope that lists everything is a list. Regions below this are real
    # but not worth a line on a page meant to be read at a glance; they are
    # counted instead, so nothing disappears silently.
    FLOOR = 2.0

    hot = [x for x in rows if x["r"] > 0.5]
    # The split that matters. Same pressure, opposite instruction: one needs
    # somebody, the other already has somebody.
    stalled = sorted([x for x in hot if x["b"] < 0.5], key=lambda x: -x["r"])
    working = sorted([x for x in hot if x["b"] >= 0.5], key=lambda x: -x["r"])
    quiet = sorted(x["name"] for x in rows if x["r"] <= 0.5 and x["b"] < 0.5)

    out: list[str] = []
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    out.append("RELATIVITY PIXELS — fleet field")
    out.append(f"{stamp} · {len(meta)} projects · {len(rows)} regions "
               f"· {len(stalled)} stalled · {len(working)} in hand "
               f"· {len(quiet)} quiet")

    def block(title: str, note: str, items: list[dict]) -> None:
        out.append("")
        out.append(f"{title}  — {note}")
        if not items:
            out.append("  (none)")
            return
        shown = [x for x in items if x["r"] >= FLOOR]
        for x in shown:
            dens = f"{x['density']:>5.1f}" if x["size"] else "    —"
            out.append(f"  {x['name']:<26} R{x['r']:>6.1f} B{x['b']:>7.1f} "
                       f"/{x['size']:<3} ={dens}  {x['profile']:<9} {x['why']}")
        rest = len(items) - len(shown)
        if rest:
            out.append(f"  … and {rest} more under R{FLOOR:g}")

    block("STALLED", "pressure with nobody on it — look here first", stalled)
    block("IN HAND", "loud, but someone is already working it", working)

    # Project shape: the standing facts. These do not decay and are not news;
    # they are the context that makes the rows above mean anything.
    out.append("")
    out.append("PROJECTS")
    out.append(f"  {'name':<11} {'kind':<5} {'files':>6} {'src':>5} {'tests':>6} "
               f"{'vend':>6} {'docs':<5} {'last commit':<12} regions")
    for name in sorted(meta):
        m = meta[name]
        kids = sum(1 for x in rows if x["name"] == name or x["name"].startswith(name + "/"))
        docs = "yes" if any(
            "documented" in x["sources"] for x in rows if x["name"] == name
        ) else "—"
        tests = m.get("tests", 0)
        flag = "  <— none" if m.get("source") and not tests else ""
        vend = m.get("vendored", 0)
        out.append(f"  {name:<11} {m.get('kind','?'):<5} {m.get('files',0):>6} "
                   f"{m.get('source',0):>5} {tests:>6} {vend:>6} {docs:<5} "
                   f"{m.get('last_commit','?'):<12} {kids}{flag}")

    shapes = _constellations(rows, meta)
    if shapes:
        out.append("")
        out.append("PATTERNS  — only visible with the whole fleet in one frame")
        for s in shapes:
            out.append(f"  · {s}")

    if quiet:
        out.append("")
        out.append(f"QUIET  — {len(quiet)} regions with nothing to say; "
                   f"skipping them is the point")
        line = "  "
        for n in quiet:
            if len(line) + len(n) > 88:
                out.append(line)
                line = "  "
            line += n + "  "
        if line.strip():
            out.append(line)

    return "\n".join(out)


def _constellations(rows: list[dict], meta: dict) -> list[str]:
    """Statements that are only true of the fleet, not of any one project.

    This is the layer that justifies keeping every project in one field instead
    of one field per project. A region's own numbers can be read from the region;
    "three of eight projects have no tests at all" cannot be read from anywhere
    except here.

    Every rule is guarded to stay silent unless it is both true and worth a line.
    A patterns section that always finds something is a horoscope.
    """
    out: list[str] = []
    if not rows:
        return out

    # --- concentrated rot: small regions carrying disproportionate pressure.
    #     These are invisible to the ranking, which is by absolute magnitude and
    #     so is partly just a ranking of size.
    # Minimum size guards against catch-all regions, which hold a handful of
    # stray files and so produce spectacular densities that mean nothing.
    dense = [x for x in rows if x["size"] >= 5 and x["density"] >= 0.5
             and x["r"] >= 2.0]
    ranked_dense = sorted(dense, key=lambda x: -x["density"])[:3]
    if ranked_dense:
        listed = ", ".join(f"{x['name']} ({x['r']:.0f} over {x['size']} files)"
                           for x in ranked_dense)
        out.append(f"densest pressure per file: {listed} "
                   f"— small places in poor condition, easy to miss by size alone")

    # --- where the pressure actually is
    total_r = sum(x["r"] for x in rows)
    if total_r > 0:
        ranked = sorted(rows, key=lambda x: -x["r"])
        top2 = sum(x["r"] for x in ranked[:2])
        if top2 / total_r > 0.55 and len(ranked) > 4:
            names = " and ".join(x["name"] for x in ranked[:2])
            out.append(f"{top2/total_r*100:.0f}% of all pressure sits in "
                       f"{names} — the rest of the fleet is background")

    # --- structural blind spots: no tests at all
    untested = [n for n, m in meta.items() if m.get("source", 0) >= 10
                and not m.get("tests")]
    if untested:
        biggest = max(untested, key=lambda n: meta[n].get("source", 0))
        out.append(f"no tests at all in {len(untested)} of {len(meta)} projects: "
                   f"{', '.join(sorted(untested))} "
                   f"({meta[biggest].get('source')} source files in {biggest} alone)")

    # --- dormancy
    def _dormant(when: object) -> bool:
        if not isinstance(when, str):
            return False
        if "mo ago" in when:
            return True
        head = when.split("d ago")[0]
        return "d ago" in when and head.isdigit() and int(head) >= 14

    dormant = sorted(n for n, m in meta.items() if _dormant(m.get("last_commit")))
    if dormant:
        listed = ", ".join(f"{n} ({meta[n]['last_commit']})" for n in dormant)
        out.append(f"untouched for two weeks or more: {listed}")

    # --- the field's own blind spots. Knowing what it cannot see is worth more
    #     than another reading of what it can.
    no_health = [x["name"] for x in rows if x["r"] > 2.0 and x["g"] <= 0.0]
    if len(no_health) >= 3:
        out.append(f"{len(no_health)} regions carry pressure with no health signal "
                   f"of any kind — nothing has vouched for them either way")

    unreviewed = sorted(n for n, m in meta.items() if not m.get("reviewed"))
    if unreviewed:
        out.append(f"never reviewed: {', '.join(unreviewed)} "
                   f"— absence of findings there means absence of looking")

    # Reviews that no longer describe the code. Distinct from never-reviewed and
    # far easier to miss, because the findings look perfectly current.
    outdated = sorted((n for n, m in meta.items()
                       if m.get("reviewed") and not m.get("review_current")),
                      key=lambda n: -meta[n].get("review_drift", 0))
    if outdated:
        listed = ", ".join(f"{n} (+{meta[n].get('review_drift', 0)} commits)"
                           for n in outdated)
        out.append(f"review has fallen behind the code in {listed} "
                   f"— those findings describe an older version")

    no_ci = sorted(n for n, m in meta.items()
                   if not any("ci" in x["sources"] for x in rows if x["name"] == n))
    if len(no_ci) >= 2:
        out.append(f"nothing checks {len(no_ci)} of {len(meta)} projects automatically: "
                   f"{', '.join(no_ci)}")

    stale_tests = sorted(n for n, m in meta.items()
                         if str(m.get("tests_verdict", "")).startswith("stale"))
    if stale_tests:
        detail = ", ".join(
            f"{n} {meta[n]['tests_verdict'][len('stale '):]}" for n in stale_tests)
        out.append(f"last test run is older than the code in {detail} "
                   f"— not counted as failing, but nothing has vouched for it either")

    # --- work at risk
    # Rolled up to projects: nested regions all report the same edit, and three
    # lines naming parent, child and grandchild is one fact wearing a disguise.
    def projects_of(names) -> list[str]:
        return sorted({n.split("/", 1)[0] for n in names})

    at_risk = projects_of(x["name"] for x in rows if x["sources"].get("unpushed"))
    if at_risk:
        out.append(f"unpushed work in {', '.join(at_risk)} — exists only here")
    wip_at = projects_of(x["name"] for x in rows if x["sources"].get("wip"))
    if wip_at:
        n = sum(x["sources"].get("wip", 0) for x in rows) / 1.5
        out.append(f"{n:.0f} uncommitted files in {', '.join(wip_at)}")

    # --- debt concentration
    debt = [(x["name"], x["sources"]["todo"] / 0.5)
            for x in rows if x["sources"].get("todo")]
    if debt:
        total_debt = sum(d for _, d in debt)
        top_name, top_n = max(debt, key=lambda kv: kv[1])
        if total_debt >= 10 and top_n / total_debt > 0.6:
            out.append(f"{top_n:.0f} of {total_debt:.0f} debt markers fleet-wide "
                       f"are in {top_name}")

    return out


def _why(r, gr, b) -> str:
    """One clause explaining the reading, in the sensors' own terms."""
    def plural(n: float, one: str, many: str) -> str:
        return f"{n:.0f} {one if abs(n - 1) < 0.01 else many}"

    bits = []
    src = r.standing_by_source()
    if src.get("ester_open"):
        # Severity-weighted, so it is not a count and must not be printed as
        # one. Files carrying findings is the honest count; the weight says how
        # much they matter.
        files = len(r.level_keys.get("ester_open", {}))
        bits.append(f"{plural(files, 'file', 'files')} with findings "
                    f"(weight {src['ester_open']:g})")
    if src.get("tests_failing"):
        bits.append(plural(src["tests_failing"] / 3.0, "test file failing",
                           "test files failing"))
    if src.get("todo"):
        bits.append(plural(src["todo"] / 0.5, "debt marker", "debt markers"))
    if src.get("unpushed"):
        bits.append(plural(src["unpushed"] / 2.0, "unpushed commit", "unpushed commits"))
    if src.get("conflicts"):
        bits.append(plural(src["conflicts"] / 6.0, "CONFLICT MARKER committed",
                           "files with CONFLICT MARKERS committed"))
    if src.get("lock_drift"):
        bits.append("lockfile behind manifest")
    if src.get("giants"):
        bits.append(plural(src["giants"] / 1.0, "outsized file", "outsized files"))
    if src.get("heavy_files"):
        bits.append(plural(src["heavy_files"] / 1.0, "oversized file",
                           "oversized files"))
    wip = b.standing_by_source().get("wip")
    if wip:
        bits.append(plural(wip / 1.5, "file uncommitted", "files uncommitted"))
    if not bits:
        bits.append("recent repair commits" if r.read(time.time())[1] > 0 else "—")
    return " · ".join(bits)


def grid(field: Field, now: float | None = None) -> str:
    """The human view. Explicitly not load-bearing -- if it disagrees with the
    ranking, the ranking is right and the render is decoration."""
    now = time.time() if now is None else now
    lines = []
    for name, _ in field.rank(now):
        g = field.groups[name]
        cells = "".join(_band(g.channels[c].magnitude(now), c) for c in CHANNELS)
        r = g.channels["R"]
        prof = r.profile(now)
        note = f"{prof} +{r.level:.0f} standing" if r.level > 0 else prof
        lines.append(f"  {cells}  {name:<28} {note}")
    return "\n".join(lines) if lines else "  (all cold)"
