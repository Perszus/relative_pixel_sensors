"""Ad-hoc: what does the field actually say right now, all three channels."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

with open("view.json", encoding="utf-8") as fh:
    v = json.load(fh)

rows = [g for g in v["groups"] if any(g["rgb"]) or g["standing"]]
print(f"{'group':<24} {'R':>7} {'G':>7} {'B':>7}  {'profile':<10} sources")
print("-" * 100)
for g in rows:
    src = " ".join(f"{k}={x:g}" for k, x in sorted(g.get("sources", {}).items()))
    print(f"{g['name']:<24} {g['rgb'][0]:>7.1f} {g['rgb'][1]:>7.1f} "
          f"{g['rgb'][2]:>7.1f}  {g['profile']:<10} {src}")

print(f"\n{len(rows)} groups with signal, {len(v['groups'])} total")
print("\n--- projects, with shape ---")
print(f"{'project':<12} {'kind':<6} {'files':>6} {'src':>5} {'tests':>6} {'last commit':>12}")
for g in sorted(v["groups"], key=lambda d: d["name"]):
    m = g.get("meta")
    if not m:
        continue
    print(f"{g['name']:<12} {m['kind']:<6} {m['files']:>6} {m['source']:>5} "
          f"{m['tests']:>6} {m['last_commit']:>12}")
