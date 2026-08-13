"""What is each sensor actually contributing, fleet-wide?

Weights decide the whole ranking, so leaving them at whatever seemed reasonable
while writing them is not a calibration -- it is a guess wearing a number. This
prints the real distribution so they can be set against something.
"""
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collect import DEFAULT_FLEET, build_router, load_json
from rp.store import CHANNELS, UNROUTED, Field

field = Field.load("field.json", build_router(load_json("fleet.json", DEFAULT_FLEET)))
now = time.time()

per_sensor = defaultdict(float)
per_sensor_n = defaultdict(int)
per_channel = defaultdict(float)
chan_of = {}

for name, g in field.groups.items():
    if name == UNROUTED:
        continue
    for c in CHANNELS:
        ch = g.channels[c]
        per_channel[c] += ch.magnitude(now)
        for src, v in ch.standing_by_source().items():
            per_sensor[src] += v
            per_sensor_n[src] += 1
            chan_of[src] = c
    # events have no source label; attribute by channel
    per_sensor[f"(events {c})"] = per_sensor.get(f"(events {c})", 0.0)

for c in CHANNELS:
    decayed = sum(g.channels[c].read(now)[1] for n, g in field.groups.items()
                  if n != UNROUTED)
    per_sensor[f"(decaying events)→{c}"] = decayed
    chan_of[f"(decaying events)→{c}"] = c

print("CHANNEL TOTALS — these must be comparable for the quadrant read to work")
for c in CHANNELS:
    print(f"  {c}  {per_channel[c]:9.1f}")

print("\nPER-SENSOR CONTRIBUTION")
print(f"  {'sensor':<26} {'ch':<3} {'total':>9} {'regions':>8} {'share of ch':>12}")
for src in sorted(per_sensor, key=lambda s: -per_sensor[s]):
    if per_sensor[src] <= 0:
        continue
    c = chan_of.get(src, "?")
    share = per_sensor[src] / per_channel[c] * 100 if per_channel.get(c) else 0
    print(f"  {src:<26} {c:<3} {per_sensor[src]:9.1f} {per_sensor_n[src]:>8} "
          f"{share:>11.0f}%")

print("\nREGIONS WITH NO HEALTH SIGNAL")
none_g = [n for n, g in field.groups.items()
          if n != UNROUTED and g.channels["G"].magnitude(now) <= 0]
print(f"  {len(none_g)} of {len(field.groups) - 1}")
