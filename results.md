# Stage 1 results — the M-series

Run 2026-08-13. CPython 3.14.7, Windows 11, against the real fleet (8 repos, 233,964
files, 180 days of git history). Harness: `run_experiments.py`. Implementation: `rp/`.

**10 of 11 pass. One genuine failure: M7, footprint — the design is 12× larger than the
document claimed.** The consequence is negligible and the claim has been corrected.

| # | Test | Verdict |
|---|---|---|
| M1 | routing coverage | PASS — 100.00%, 0 unrouted |
| M2 | lazy decay correctness | PASS — worst relative error 2.7e-11 |
| M3 | ring behaviour | PASS — 7/7 properties |
| M4 | ingest O(1) in history | PASS — 1.02× drift over a 30× history increase |
| M5 | read O(groups) | PASS — flat in history, 0.98× ideal slope in groups |
| M6 | idle cost zero | PASS (structural; 24h soak not run) |
| M7 | footprint | **FAIL — 227 KB vs ~18 KB predicted** |
| M8 | signal volume | PASS — ~16 signals/day fleet-wide |
| M9 | parasitic attachment | PASS (qualified) — builds need a wrapper |
| M10 | temporal profiles | PASS — 5/5, including warming |
| M11 | end-to-end | PASS — 2,872 real signals, L1 32 tokens in 0.08 ms |

---

## Answers to the three questions

**Is it actually quick?** Yes, and by a margin that makes the question uninteresting.
Ingest is 889 ns/event in CPython — 18× worse than the document's estimate and still
irrelevant, because at the fleet's measured volume one full day of signals costs **14
microseconds**. A fleet-wide Layer 1 read is 0.08 ms. Reading the whole 4096-group
ceiling is 3.9 ms.

**Does it eat little to nothing in resources?** Resources, yes: 227 KB at fleet scale,
17 KB for the real fleet as it stands. Idle, yes and structurally so: no threads, no
timers, no schedulers, no `while True`, no atexit hooks — verified by AST scan, not
assertion. There is no process to measure.

**Can the db be built and fed signals?** Yes. 54 routing rules derived from the
directory tree in 0.8 ms give 100% coverage over 233,964 files with zero unrouted. Two
real sensors of different statement types were attached and 2,872 real signals ingested:
git history (event) and Ester findings (judgment). The store saves, reloads, and serves
identically.

**Verdict: the relativity pigments work as theorised.** Not the numbers as theorised —
several of those were wrong — but every mechanical property the design depends on holds.

---

## What was found

### The design got simpler under implementation

2.0 §6.1 describes the vortex as rings that **fold** into each other, RRD-style. That
needs fold triggers, boundary handling, and an answer to "is this datum counted once?"

None of it is necessary. Make the rings **independent accumulators at different
half-lives**, each receiving every event, and you get identical behaviour — bounded
mass, O(1) ingest, the full temporal gradient — with no folding, no boundaries, and no
possible double-count. M3 was reformulated around this: it now tests properties rather
than machinery, because the machinery does not exist.

M3 confirms the properties directly: a single event lands as exactly its mass in every
ring; 200k events over 3.8 simulated years stay bounded; 50 years of dormancy decays to
hard zero rather than a denormal crawl; a backwards clock jump cannot inflate a value.

### Two of the five rings carry no information at this fleet's volume

The most useful thing the run produced, and it is not visible from the whiteboard.

- **The hour ring is noise.** At ~16 signals/day the event interval exceeds its
  half-life, so it oscillates between "just fired" and "nearly zero". Fed a constant
  stream it should report exactly — it reports **1.37× high**, and what it is really
  measuring is how long ago you sampled.
- **The year ring is not saturated.** It needs 2–3 half-lives to fill. After three
  simulated years of constant load it still reads **0.86× low**. Classify on it and
  every steady workload looks like it is warming for the system's first several years.

The working range is **day / week / month**, which agree to within 1.4% on a constant
stream. Both excluded rings are still stored — two floats each — because they become
meaningful at other volumes. But nothing is read off them.

This is a genuine cold-start property. It would have shipped as a mystery.

### The classifier was wrong, and only synthetic patterns exposed it

The first version compared the shortest ring to the longest and used the middle ring's
lift to separate a burst from a ramp. It classified **a burst as warming** — the two
most operationally different readings in the whole table, confused with each other.

The fix: a burst dumps everything into the shortest working ring, while a ramp lifts
each ring roughly in proportion. So the *shape* of the rise separates them, not its
size. Now 5/5, and the separation is wide (burst 7.1× steepness, ramp 2.0×, threshold 4).

Worth noting what M10 actually establishes: **warming is detectable**, and it is
detectable from five floats. A single magnitude cannot express it at any price. That is
the concrete thing the rings buy, and it is now measured rather than argued.

### One real implementation bug, found by a test that was passing

`t = 0.0` was the sentinel for "never written". Epoch zero is a legal timestamp, so any
group whose first event landed there read as permanently cold. It faked a pass in M3
(retention ordering read `0 < 0 < 0 < 0 < 0` and satisfied the assertion vacuously) and
faked a failure in M5 (unwritten groups short-circuit their read, flattering the
low-history case). Sentinel is now `None`, with an explicit regression check.

The pattern is worth keeping in mind: **a test that passes on all-zeros is not passing.**

### Reads must not run backwards

Discovered while fixing the M2 harness. A read at a time *earlier* than the last write
returned a value containing events that had not happened yet. Now clamped — the field
has no opinion about times it has already passed — with a regression check.

### Sensors are cheap, but not free in the way the document implied

M9 is a qualified pass. Git and Ester attach for genuinely zero marginal cost: both
already produce the verdict as a by-product, and Ester's reports are sitting on disk
right now with 83 parseable `area:` lines across 14 files. Nineteen open findings routed
straight into the pressure channel without a single scan.

Builds and tests are different. Tapping them requires **wrapping the launch command**.
That is still parasitic — no scan, no schedule, no process — but it is an integration
cost per toolchain, and it fails *silently* if someone builds from the IDE instead of
the wrapper. Silent sensor failure is the dangerous kind, because §8.4 reads absence of
signal as safety. **"Unregistrable" survives; "zero-effort" does not.**

### The footprint claim was wrong by 12×

The only outright failure. Predicted ~18 KB at 300 groups; measured **227 KB**.

The prediction counted five float32 rings per channel and nothing else. Reality adds
64-bit floats, a per-channel timestamp, and — dominating at ~75% of resident size — the
bounded top-k pointer dict at ~580 B/group.

That memory buys Layer 2. Pointers are what let a reader reject "loud but trivial"
before spending anything, which 2.0 §8.3 classifies as a safety requirement rather than
a convenience. **Decision: accept the cost, correct the claim.** 227 KB is still
cache-resident and still nothing. Keys are now stored relative to their group, since the
absolute prefix is already implied by the group name.

### The map is legible, and it is small

The real fleet, end to end, from git history and Ester reports:

```
field: paranoia/src-^ veil/frontend.v orobos/tools.v thisnote/lib.v
       huts/huts_ui.v thisnote/windows.v huts/crates.v  (33 cold)
```

32 tokens for the whole fleet, in 0.08 ms — under the 50–100 predicted. It names no
nouns and makes no claims: magnitude, direction, and the fact that 33 of 40 groups are
cold. One region reads `^` (warming); the rest are cooling or quiet.

Layer 2 for a single group is 81 tokens, under the 200–400 predicted, and carries
channel, profile, concentration and three pointers.

---

## What Stage 1 did not establish

The M-series is a test of the instrument, not of what it measures. Everything below is
still open, and nothing here should be read as evidence for any of it:

- **Whether the readings mean anything.** That `paranoia/src` reads warming is a fact
  about its commit pattern. Whether that predicts anything worth knowing is Stage 2 (E0),
  and the bar is beating "rank by most-recently-changed".
- **Whether cold is trustworthy.** §8.4 rests entirely on the false-negative rate, and
  nothing here measured it. M1's 100% routing coverage is necessary for it, not
  sufficient.
- **Whether the machinery beats a counter.** E2 exists to let the elegant parts lose.
  Nothing in Stage 1 tested the elaborate model against `Counter()` plus a timestamp.
- **Idle cost over real time.** M6 is the structural check — which is what the claim
  rests on — but the 24h soak in `experiments.md` was not run.
- **Sensor calibration.** Two sensors were attached to prove signals flow. Their masses
  (severity 1 = 4.0, severity 3 = 1.0) are guesses, and the Ester sensor is knowingly
  incomplete: it accumulates judgments but never clears them when a finding closes,
  which is exactly the failure mode 2.0 §4.3 Q6 warns kills the tool.

---

## Always-on layer (added after Stage 1)

`collect.py` is the absorption pass. It owns no process: it runs, does O(new signals)
work, and exits. Between runs the field is still current, because state is a function of
time — **"always on" does not mean "always running"**, which is the property that makes
the whole thing affordable.

- Bootstrap (180 days, 8 repos): 2,853 file-touches + 19 standing findings, **1.03 s**.
- Steady-state run with nothing new: **265 ms**, almost all of it `git rev-parse`.
- Idempotent: watermarked per repo by HEAD sha, so a second run absorbs zero.
- Git hooks were the obvious tap and are **unusable on this machine** (`sh.exe`
  0xC0000142). Watermarks give exactly-once delivery without them.

Two defects surfaced the moment it ran on real data — both in the serving layer, which is
2.0 §11's predicted cause of death:

**Standing judgments were being reported as cooling.** `thisnote/lib` carries 17 units of
unresolved Ester findings and zero recent activity. It read `cooling` — "calming down" —
because `profile()` describes activity, and activity is the wrong axis when the reason a
region is loud is that nothing is happening there. That is exactly the red-without-blue
quadrant §7 calls the highest-value reading, inverted into the most reassuring one.
Standing judgments now override the temporal mark.

**The bands were mis-calibrated by an order of magnitude.** Edges of 0.5/2/6 against a
measured distribution with median 4 and max 67 put 7 of 18 nonzero groups in the top
band. The loudest symbol carried no information. Now log-spaced (1/4/16), and
deliberately still absolute rather than quantile-based — quantiles would guarantee an even
spread of symbols and so would paint a quiet fleet as though something were happening.

Also visible and not yet addressed: the **G (health) channel is empty fleet-wide** — no
health sensor exists yet — and the pressure channel's temporal profiles are almost all
`spike`, because fix-commits are sparse and bursty. The temporal structure currently lives
in the activity channel, not the pressure channel.

## The sensor set (2026-08-13)

Fifteen sensors, ~1 s a steady-state pass. `python collect.py --sensors` prints the spec
sheet; `rp/sensors.py` `REGISTRY` is the source of truth.

| | R — pressure | G — health | B — activity |
|---|---|---|---|
| **event** | fixes | | commits |
| **standing** | todo, unpushed, ester_open, tests_failing, lock_drift, heavy_files, conflicts, giants | ester_clean | wip |
| **state** | | tested, documented, ci | |

Three rules the set enforces, each of which came from a bug rather than a plan:

**Pressure filters to what is ours; activity does not.** Thirty-eight "debt markers in
orobos" were all Google's, inside the vendored Oboe library. Not false — *about somebody
else*, which is worse, because it reads as actionable. `is_ours()` now gates every pressure
sensor. The same leak had orobos at "212 source files, 0 tests" when it is really 74, and
counted purity's two tests which live in `archive/`. Activity stays unfiltered: bumping a
vendored library is real work and worth seeing.

**A verdict older than the code is not a verdict.** Paranoia's pytest cache lists five
failing test files, dated seventeen days ago. Published naively that is live pressure; in
fact it is an opinion about a snapshot the code has since left. `verdict_age()` measures days
elapsed and commits landed since, and discounts past 3 days or 10 commits. It applies to
every derived verdict — test runs, Ester reviews, lint passes.

**A test that always fires is a horoscope.** The first version of the staleness rule asked
"has the code moved since the verdict", which is true of all eight projects always. It now
measures *drift* and fires for one. Any pattern that cannot stay silent is decoration.

### Denominator

Regions carry a file count and a density (pressure per file), because a ranking by absolute
magnitude is partly a ranking of size, and the same large regions would win permanently
regardless of condition (2.0 §4.3 Q4). Both are shown rather than one chosen: ten findings
cost ten findings' worth of work wherever they are, but ten findings in a nine-file region is
a different *kind* of news. The density ranking does surface different regions than the
magnitude ranking, which is the whole reason it exists.

The denominator counts every tracked file, not only source. Sizing the *routing* by source is
right — it decides where boundaries fall — but pressure lands on non-source files too, and
dividing those by a source-only count produced a density of 31.8 for a region holding one
source file and two stray binaries.

## Files

| Path | What |
|---|---|
| `rp/vortex.py` | Lazily-decayed multi-half-life accumulators; profile classification |
| `rp/store.py` | Groups, channels, path routing, persistence |
| `rp/serve.py` | Layer 1, Layer 2, grid render |
| `rp/sensors.py` | The sensor set, its weights, and the spec-sheet registry |
| `collect.py` | Absorption pass. `--read` queries without absorbing, `--sensors` prints the spec sheet |
| `BRIEF.txt` | The telescope view — whole fleet on one screen, rewritten every pass |
| `run_experiments.py` | The M-series harness |
| `peek.py` | Ad-hoc: the pressure channel's current distribution |
| `fleet.json` | Which repos map to which group labels |
| `field.json` / `watermarks.json` | The live field, and per-repo exactly-once markers |

Run with `python run_experiments.py` from the project root. Takes about 25 seconds,
most of it `git log` subprocesses.
