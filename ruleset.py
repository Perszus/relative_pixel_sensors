"""Print the rule table. `python ruleset.py`"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rp import rules

by = defaultdict(list)
for r in (*rules.RULES, *rules.MACHINE_RULES):
    if r.chan and r.w:
        by[r.on].append(r)

for kind in sorted(by, key=lambda k: (k != "*", k)):
    rec = next((x for x in rules.RECOGNIZERS if x.kind == kind), None)
    what = rec.describes if rec else "anything"
    print(f"\n{kind}  — {what}")
    for r in by[kind]:
        args = ", ".join(str(a)[:38] for a in r.args)
        print(f"  {r.chan}  {r.id:<20} {r.probe}({args})")
        print(f"     {'':<20} → {r.says}")

n = sum(len(v) for v in by.values())
print(f"\n{n} emitting rules across {len(by)} scopes, "
      f"{len(rules.RECOGNIZERS)} recognizers")

if "--live" in sys.argv:
    # Which rules actually fire, and which never do anywhere.
    #
    # Silence is the desired default for a nociceptor and a symptom for
    # everything else, and the two are indistinguishable from the table alone.
    # A malformed git format string once silenced an entire probe kind across
    # the whole fleet, and nothing noticed because "no findings" is what a
    # healthy rule looks like.
    import os
    from collections import Counter

    from collect import load_fleet
    from rp import probes

    fleet = {r: l for r, l in load_fleet().items() if os.path.isdir(r)}
    fired = Counter()
    for repo, label in fleet.items():
        for f in rules.evaluate(label, repo, rules.recognize(repo)):
            fired[f.rule] += 1
    for f in rules.machine():
        fired[f.rule] += 1

    print(f"\nfired across {len(fleet)} subjects:")
    for rid, count in fired.most_common():
        print(f"  {rid:<24} {count}")

    silent = sorted(r.id for r in (*rules.RULES, *rules.MACHINE_RULES)
                    if r.chan and r.w and r.id not in fired)
    print(f"\nsilent everywhere ({len(silent)}):")
    for rid in silent:
        rule = next(r for r in (*rules.RULES, *rules.MACHINE_RULES) if r.id == rid)
        # Does the probe answer at all, on any subject it is scoped to?
        answered = scoped = False
        for repo, label in fleet.items():
            if rule.on != rules.ANY and rule.on not in rules.recognize(repo):
                continue
            scoped = True
            fn = probes.PROBES.get(rule.probe)
            try:
                v = float(fn(repo, *rule.args))
            except Exception:
                v = float("nan")
            if v == v:            # not NaN — the probe answered
                answered = True
                break
        # Three states, not two. "Nothing here is a Docker project" is not the
        # same as "the probe is broken", and the first version reported them
        # identically — which would have buried a real dead probe among rules
        # that are silent for the honest reason.
        if not scoped:
            state = "no subject of this kind"
        elif answered:
            state = "quiet"
        elif rule.needs:
            # UNKNOWN by design, not by fault. The rule is waiting on evidence
            # nobody has produced yet, which is a different thing from a probe
            # that cannot answer — and the two look identical from outside.
            state = f"awaiting evidence — {rule.needs}"
        else:
            state = "PROBE NEVER ANSWERS"
        print(f"  {rid:<24} {state}")
