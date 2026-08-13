"""Why does the field say that?

    python explain.py machine            every finding on a subject
    python explain.py thisnote/lib       "
    python explain.py --rule no-ci       one rule, everywhere it fires
    python explain.py --loudest          the ten largest contributions in the field

For each finding: the rule, the probe it named, the arguments it was given, what
that probe returns when run again right now, and the one sentence that would
falsify it.

This exists because a confidently wrong reading is only survivable if checking
it is cheap. A test suite was reported as broken; establishing that it was in
fact passing took twenty minutes of manual digging, and nothing about the
finding pointed at the evidence it rested on. With sixty-seven rules, most
findings are never going to be audited by hand — so each one has to be able to
account for itself on demand.

The re-run matters more than the record. A finding that cannot be reproduced
now is either stale or wrong, and both are worth knowing.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collect import build_router, load_fleet
from rp import probes, rules
from rp.store import Field

HERE = os.path.dirname(os.path.abspath(__file__))


def _fleet_roots() -> dict[str, str]:
    return {label: repo for repo, label in load_fleet().items()
            if os.path.isdir(repo)}


def _rule(rule_id: str):
    return next((r for r in (*rules.RULES, *rules.MACHINE_RULES)
                 if r.id == rule_id), None)


def _falsifier(rule) -> str:
    """What would make this finding go away. Stated as an action, because a
    finding a reader cannot act on or disprove is just an opinion."""
    if rule is None:
        return "unknown rule — it may have been renamed or removed"
    probe = rule.probe
    args = ", ".join(str(a) for a in rule.args)
    if probe in ("exists", "count", "bytes_over"):
        return f"`{args}` no longer matched anything under the subject"
    if probe == "absent":
        return f"`{args}` existed"
    if probe == "content":
        return (f"no line matched /{rule.args[-1]}/ in `{rule.args[0]}` "
                f"— add `rp:allow` to a line to exclude it deliberately")
    if probe.startswith("run_"):
        return "a newer receipt at the current HEAD recorded success"
    if probe == "vulnerable_deps":
        return "the advisory snapshot no longer listed a declared dependency"
    if probe in ("failing_test_share",):
        return "a fresh pytest run recorded no failures"
    if probe == "log_errors":
        return "no error line newer than the last commit remained in the logs"
    return f"`{probe}({args})` returned zero or UNKNOWN"


def _rerun(rule, root: str) -> str:
    fn = probes.PROBES.get(rule.probe)
    if fn is None:
        return "probe no longer exists"
    try:
        raw = float(fn(root, *rule.args))
    except Exception as e:
        return f"raised {type(e).__name__}: {e}"
    if raw != raw:
        return "UNKNOWN — the probe declines to answer"
    return f"{raw:g}  (x weight {rule.w:g}, capped at {rule.cap:g})"


def explain_subject(field: Field, name: str, roots: dict[str, str]) -> None:
    group = field.groups.get(name)
    if group is None:
        near = [n for n in field.groups if name.lower() in n.lower()]
        print(f"no subject called {name!r}."
              + (f" did you mean: {', '.join(sorted(near)[:5])}" if near else ""))
        return

    label = name.split("/", 1)[0]
    root = roots.get(label, "")
    print(f"\n{name}")
    print("=" * min(len(name), 78))

    for channel in ("R", "G", "B"):
        ch = group.channels[channel]
        sources = ch.standing_by_source()
        decayed = ch.read(__import__("time").time())[1]
        if not sources and decayed <= 0:
            continue
        print(f"\n  {channel}  magnitude {ch.magnitude(__import__('time').time()):.2f}")
        if decayed > 0:
            print(f"      {decayed:8.2f}  (decaying events — commits, repairs)")
        for source, value in sorted(sources.items(), key=lambda kv: -kv[1]):
            if not source.startswith("rule:"):
                print(f"      {value:8.2f}  {source}  (hand-written sensor)")
                continue
            rule = _rule(source[5:])
            print(f"      {value:8.2f}  {source[5:]}")
            if rule is None:
                # Machine findings are synthesised per volume and per service
                # inside rules.machine(), so their ids cannot appear in a static
                # table — `disk-y-low` only exists because a Y: drive does.
                # They carry their own words instead, and saying "renamed or
                # removed" about them was both wrong and alarming.
                said = ch.level_keys.get(source, {})
                for phrase in sorted(said, key=said.__getitem__, reverse=True)[:3]:
                    print(f"                reads    {phrase}")
                print("                measured live each pass; a level, not an "
                      "accumulation")
                continue
            args = ", ".join(repr(a) for a in rule.args)
            print(f"                probe    {rule.probe}({args})")
            print(f"                says     {rule.says}")
            if root:
                print(f"                now      {_rerun(rule, root)}")
            print(f"                false if {_falsifier(rule)}")


def explain_rule(rule_id: str, roots: dict[str, str]) -> None:
    rule = _rule(rule_id)
    if rule is None:
        print(f"no rule called {rule_id!r}")
        return
    args = ", ".join(repr(a) for a in rule.args)
    print(f"\n{rule.id}")
    print("=" * min(len(rule.id), 78))
    print(f"  scope     {rule.on}")
    print(f"  probe     {rule.probe}({args})")
    print(f"  channel   {rule.chan}   weight {rule.w:g}   cap {rule.cap:g}")
    print(f"  says      {rule.says}")
    if rule.needs:
        print(f"  needs     {rule.needs}")
    print(f"  false if  {_falsifier(rule)}")
    print("\n  evaluated now, per subject:")
    for label, root in sorted(roots.items()):
        if rule.on != rules.ANY and rule.on not in rules.recognize(root):
            continue
        print(f"    {label:<20} {_rerun(rule, root)}")


def loudest(field: Field, roots: dict[str, str], n: int = 10) -> None:
    import time as _t

    now = _t.time()
    rows = []
    for name, group in field.groups.items():
        for channel in ("R", "G", "B"):
            for source, value in group.channels[channel].standing_by_source().items():
                rows.append((value, name, channel, source))
    rows.sort(reverse=True)
    print(f"\nthe {n} largest single contributions in the field")
    print("-" * 78)
    for value, name, channel, source in rows[:n]:
        rule = _rule(source[5:]) if source.startswith("rule:") else None
        detail = rule.says if rule else "hand-written sensor"
        print(f"  {value:7.2f}  {channel}  {name:<26} {source.replace('rule:', '')}")
        print(f"           {detail}")


def main(argv: list[str]) -> int:
    roots = _fleet_roots()
    field = Field.load(os.path.join(HERE, "field.json"),
                       build_router(load_fleet()))

    if "--rule" in argv:
        i = argv.index("--rule")
        if i + 1 >= len(argv):
            print("usage: explain.py --rule <rule-id>")
            return 2
        explain_rule(argv[i + 1], roots)
        return 0
    if "--loudest" in argv:
        loudest(field, roots)
        return 0
    if not argv:
        print(__doc__)
        return 2
    explain_subject(field, argv[0], roots)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
