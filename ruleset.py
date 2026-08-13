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
