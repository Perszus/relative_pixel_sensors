> **Status, 2026-08-13.** Stage 1 ran and passed; the mechanism is proven and
> the M-series lives in `run_experiments.py`. Stage 2 has **never run** and is
> blocked on data, not effort — a replay needs a cut date with training history
> before it and a scoring window after, and this fleet's repositories are mostly
> younger than that. It is still the gate the whole idea stands or falls on.
>
> Stage 3 was written when the intended reader was an agent. That framing was
> dropped: the tool has no privileged consumer, and those experiments are kept
> only to keep consumer questions out of Stages 1 and 2.
>
> Two things this document does not describe, because they did not exist when it
> was written: the rules architecture (see [`PROBES.md`](PROBES.md)) and the
> verification tooling that grew out of actually using the thing — `audit.py`
> re-derives claims by an independent path, `explain.py` makes any single
> finding checkable, `ruleset.py --live` distinguishes a silent rule from a
> broken one. Between them they have caught more real defects than the M-series
> did, which is worth noting: the experiments proved the mechanism, and the
> tooling caught the mistakes.

# Relativity Pixels — Experiments

*Companion to `Relativity Pixels 2.0.md`. Nothing here has been run.*

Date: 2026-08-13

---

## Purpose and order

Three questions, in this order. Each stage is worthless if the one before it failed.

| Stage | Question | Series |
|---|---|---|
| **1** | **Does the mechanism work as theorised?** Is it actually quick, does it actually cost nothing, can the store be built and fed? | **M** |
| 2 | Does the field say anything worth hearing? | E |
| 3 | Does a consumer get value from it? | C — *later* |

Stage 1 is the current work. Stages 2 and 3 are recorded so the plan is complete, but
neither should be started before the M-series returns.

**What is being built.** A standalone tool with a queryable interface — not a component
of anything. It has no privileged consumer. Who reads it, and how they wire it in, is a
separate project that starts after the foundation stands on its own. Nothing in Stages 1
or 2 may assume the reader is an agent, a human, or a script; if an experiment only makes
sense for one kind of reader, it belongs in Stage 3.

**Why this order.** The M-series is deterministic — each test passes or fails cleanly,
with no baselines, no statistics and nothing to argue about. The E-series is
statistical and therefore always arguable, and it needs a working store to feed
anyway. Running E first would mean building a throwaway script that validates a
hypothesis about signals while proving nothing about the system that would actually
ship.

## Ground rules

1. Mechanism failures are **bugs**, not verdicts — with two exceptions (M8, M9) which
   can genuinely falsify the design's assumptions. Those are marked.
2. Every claim in 2.0 that can be tested mechanically should be, before any claim that
   requires interpretation.
3. Measure, don't assert. Every cost figure in 2.0 §6.3 is a prediction, and all of
   them are currently unverified.

---

# Stage 1 — Does the mechanism work?

> **RUN 2026-08-13. 10/11 pass. Full write-up in `results.md`.** M7 failed outright
> (footprint 12× the predicted figure; claim corrected, cost accepted). M9 passes with a
> qualification. M3 was reformulated during implementation — the folding rings it was
> written to test turned out to be unnecessary. Verdict: the mechanism works as
> theorised, though several of the numbers did not.

## M1 — Store construction and routing coverage

**Question.** Can the group store be built from a `path → group` config across the six
repos, and do signals actually land somewhere?

**Method.** Author the path map for the fleet. Walk every file. Measure what fraction
resolves to a group, and what falls through unrouted.

**Watch for.** The unrouted fraction is the quiet risk in 2.0 §5 — a signal whose path
matches no group is silently lost, and silent loss corrupts §8.4's claim that cold
means safe. Also measure the opposite: groups that match nothing, which are dead
config.

**Pass.** High routing coverage, and every unrouted path is explainable rather than
surprising. Unrouted signals must be *counted*, never dropped without a tally.

## M2 — Lazy decay correctness

**Question.** Is 2.0 §3.1 actually true — does decay-on-read equal stepwise decay?

**Method.** Feed an identical event stream to two implementations: the lazy one
(value + timestamp, decayed on read) and a naive reference that ticks and decays every
interval. Read both at many arbitrary points, including long gaps and points with no
events at all.

**Pass.** Values agree within float tolerance at every read point.

**Why it matters.** This is the single mathematical claim the entire cost argument
rests on. It is also trivially falsifiable, which makes it the ideal first test.

## M3 — Ring folding correctness

**Question.** Does the vortex (2.0 §6.1) conserve what it should and double-count
nothing?

**Method.** Feed known synthetic streams. After folds, compare the ring structure
against a full-fidelity record of the same stream. Specifically probe the boundaries:
a datum crossing from ring 0 to ring 1 must be counted exactly once — not twice, not
zero times.

**Pass.** Weighted mass matches the full record within the resolution loss the design
intends, and no datum is duplicated or dropped at a boundary.

**Watch for.** This is where hierarchical aggregation implementations normally have
their bugs. Edge cases: clock going backwards, a fold triggered while a ring is empty,
bursts arriving exactly at a boundary, and long dormancy spanning several rings at once.

## M4 — Ingest is O(1), independent of history

**Question.** 2.0 §3.4 claims per-event cost does not grow. True?

**Method.** Ingest 10, 10³, 10⁶, 10⁸ events. Plot per-event cost.

**Pass.** Flat. Any upward slope means something is O(history) and must be found.

## M5 — Read is O(groups), not O(events)

**Method.** Same store at wildly different history depths; measure full-fold read time.

**Pass.** Read cost tracks group count only, and is invariant to how much history the
store has absorbed.

## M6 — Idle cost is structurally zero

**Question.** Is 2.0 §9.2 (no timers, no resident process) actually honoured?

**Method.** Leave it alone for 24h. Confirm: no process in the list, no timer
registered, no scheduled task, zero wakeups, zero I/O.

**Pass.** Not "small." **Zero.** The bar is that there is nothing running to measure.

## M7 — Footprint

**Method.** Measure resident size at fleet scale (~300 groups), then scale groups up
toward the 4096 ceiling and plot growth.

**Pass.** Tens of KB at fleet scale; growth linear in groups and flat in history.
Confirms or refutes the figures in 2.0 §6.3.

## M8 — Signal volume reality check ⚠️ *can falsify*

**Question.** How many signals do six repos actually produce per day?

**Method.** Count, from history and from a few days of real activity: commits, build
warnings, test runs, Ester findings, dependency checks.

**Why it matters.** Nobody has measured this and every cost argument in 2.0 assumes it
is small. If the answer is ~50/day the cost discussion is moot and the design is
comfortably over-engineered. If some sensor emits 100k/day, ingest batching becomes
mandatory and §3.1's per-event model needs revisiting.

**Falsifies if.** Volume is high *and* bursty enough that ingest can no longer be
parasitic — at which point the sensor would need its own process, breaking §9.2.

## M9 — Can sensors actually attach parasitically? ⚠️ *can falsify*

**Question.** 2.0 §3.2 forbids scanning. Is that achievable in practice on this
toolchain?

**Method.** Attempt a real tap on each of: a Gradle/Flutter build, a test run, an
Ester pass, a git commit. Get a number out **without adding a traversal of your own.**

**Falsifies if.** The only way to obtain the values is to scan — because then the
system grows a scanner, which grows a schedule, which grows a process, and the
zero-idle-cost property is gone. This would not kill the idea, but it would demote it
from "unregistrable" to "cheap", and 2.0 §§3.2 and 9 would need rewriting.

**Note.** Ester and git are near-certain to pass. Builds are the uncertain one.

## M10 — Do the temporal profiles classify correctly?

**Question.** Does the reading table in 2.0 §6.2 work *mechanically* — before asking
whether it is predictive?

**Method.** Feed four synthetic patterns: a single burst, a sustained load, a load that
stops, a load that is accelerating. Read the ring gradient.

**Pass.** They classify as spike / sustained / cooling / warming respectively, with
clear separation and stable thresholds.

**Why it's here.** This validates the *instrument* independently of whether the
readings mean anything about code. If a known warming pattern doesn't read as warming,
no amount of good sensor design will rescue it.

## M11 — End-to-end round trip

**Method.** Real signals from one repo → routed → stored → folded → served through the
query interface as Layer 1 and Layer 2 payloads. Confirm the pipe runs end to end and
the payload sizes match 2.0 §8.1 (L1 ~50–100 tokens fleet-wide; L2 ~200–400 per project).

**Pass.** It runs, the payloads are the size the design claims, and both layers come out
of a plain query — no consumer-specific formatting, no assumptions about who is asking.

**Not being judged here.** Whether the content is any good (Stage 2) or useful to anyone
in particular (Stage 3).

---

# Stage 2 — Does the field say anything?

*Only after the M-series. Runs against the real store built in Stage 1, not a
throwaway script.*

Method for all of these is **replay**: cut the fleet's history at a date `D`, ingest
only pre-`D` signals, score against bug-fix commits in the 90 days after `D`.

| # | Question | Control | Kills if |
|---|---|---|---|
| **E0** | Does the field rank regions better than "what changed most recently"? | rank by recency; also raw churn and LOC | it doesn't beat recency — the idea reduces to `git log --stat` |
| **E6** | When it says cold, is it right? | base incident rate | cold regions fail at near base rate — nothing can be safely skipped, and 2.0 §8.4 must be struck |
| **E2** | Does the ring/channel machinery beat a plain counter + recency? | `Counter()` per group | the counter matches — delete 2.0 §§6–7 and ship the counter |
| **E1** | Which sensors actually contribute? | leave-one-out | — keep only sensors whose removal measurably hurts |
| **E3** | Does the denominator change the ranking? | raw vs per-KLOC vs per-rate | raw correlating with LOC confirms frequency bias; denominator becomes mandatory |
| **E4** | Does concentration predict patch-vs-refactor? | files touched by the actual fix | uncorrelated — keep the field, strike the claim |
| **E5** | Do the four temporal profiles have different outcomes? | incident rate per class | all alike — vortex is compression only, drop the interpretation table |

**Score by ranking, not classification** — top-k hit rate and rank correlation. The
question is always "where should attention go", which is an ordering problem.

**Expect E0 to be hard.** Beating recency is where most defect prediction dies. A
modest win that holds across four of six repos is a real result; a large win on one
repo is noise.

---

# Stage 3 — Does a consumer get value from it?

*Later, and deliberately unspecified. Recorded for completeness only. Not to be started
until Stages 1 and 2 have returned, and not to be allowed to influence the design of
either.*

A consumer is anything that reads the tool: an agent, a person at a dashboard, a script
that opens an issue. Each gets its own evaluation, because "useful" means something
different for each, and none of them is the tool's reason for existing.

The point of writing these down now is **to keep them out of Stages 1 and 2.** If a
mechanism decision starts being justified by "it would help a reader do X", that
justification belongs here, not there.

- **C1** — Agent briefing A/B. Headline metric would be **wrong-path frequency** (work
  committed to and later abandoned), not orientation cost — reliability is about the bad
  tail, not the average.
- **C2** — Layer 2 sufficiency: given L1 + L2 and no file access, can the right region be
  chosen, and is "loud but trivial" correctly rejected? Applies to any reader.
- **C3** — Stale-reference detection: flag stored notes or docs referencing paths that
  have not existed for 90+ days.
- **C4** — Human grid vs table: does a ten-second look at the render reproduce the
  table's top three? If not, the render is decoration — acceptable, but never load-bearing.
- **C5** — Fleet view: does the cross-project layer produce observations that are true and
  previously unknown? Low yield expected, high value per hit.

---

## What would falsify the whole idea

1. **M9 fails** — sensors cannot attach parasitically, so the system needs a process,
   so it is no longer unregistrable. Demotion, not death.
2. **E0 fails** — the field does not beat recency. The idea reduces to `git log`.
3. **E6 fails** — cold is not trustworthy, so nothing can be safely skipped.
4. **E2 wins** — a plain counter matches the full model, and the design is elaborate
   packaging around a frequency table.

Outcomes 2 and 3 end it. Outcome 4 does not, but requires deleting most of 2.0 and
keeping only the serving layer.

---

## Current status

**Stage 1 complete, 2026-08-13 — see `results.md`.** The mechanism holds. Ingest is
889 ns/event, a fleet-wide Layer 1 read is 0.08 ms and 32 tokens, resident state is
227 KB at 300 groups, routing covers 233,964 files with zero unrouted, and there is no
process to measure. Two real sensors feed it. Three findings changed the design: the
vortex needs no folding, only three of the five rings carry information at this fleet's
signal volume (~16/day), and the burst-vs-ramp classifier was wrong until synthetic
patterns caught it.

**Stage 2 is next, and E0 is the one that matters.** Everything proven so far is about
the instrument. Whether it *reads* anything worth reading is untested, and the bar is
beating `git log --stat`.
