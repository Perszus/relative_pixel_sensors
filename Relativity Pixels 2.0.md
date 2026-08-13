# Relativity Pixels 2.0

*Working reference. Supersedes `Relativity Pixels.md` (the 1.0 draft), which remains
useful as the origin document but describes a different system.*

Date: 2026-08-13
Status: design settled, unvalidated. Nothing built. See `experiments.md`.

---

## 1. What this is

An **attention field** over a set of systems.

It maintains, at near-zero cost, a continuously-defined sense of *where activity,
pressure and health are concentrated* across a project fleet — and serves that sense
to two consumers: an AI agent working on the projects, and the human who owns them.

It is not a database, not a monitor, not an analysis tool, and not a computational
substrate. It produces no findings, no summaries and no conclusions. It answers one
question, cheaply and always:

> **Is anything happening, and roughly where?**

The *what* is deliberately out of scope. That is the next step, and it belongs to
whoever reads the field.

### What changed from 1.0

1.0 proposed a reactive substrate — a diffusing pigment field where meaning emerges
from propagation dynamics. That version is not what this is. The diffusion, the
sequencer, the work budgets and the generation-counter machinery were all load-bearing
only for the emergent-computation framing, and all of it is gone (§10).

What survived is the instinct underneath it: **a pre-semantic, spatially-organised,
always-on sense of a system's state, cheap enough to be ambient.** That instinct is
correct and is the whole design.

---

## 2. Purpose and the division of labour

The field's value comes from a strict separation:

> **The field is a gradient sensor. The reader is the investigator.
> Neither does the other's job.**

The field never interprets. That is not a limitation to be lifted later — it is the
property that makes the system simultaneously *cheap*, *general*, and *safe*:

- **Cheap** — interpretation is the only expensive operation. Excluded, nothing costs.
- **General** — a field that reports only magnitude does not need to know what any
  project *is*. Audio DSP, a Cloudflare Worker and a Python watcher all emit the same
  kind of number. Heterogeneity stops mattering.
- **Safe** — a pointer that is wrong costs one wasted look. A summary that is wrong
  costs a wrong action. The field cannot mislead about content because it makes no
  claim about content.

Generality and cheapness normally trade against each other. Here they don't, because
meaning is deferred to a consumer who is going to be present anyway and who needs it
for only the one region that turned out to be warm.

### The problem it actually addresses

Not token cost. The real defect it targets is **blind triage**:

When an agent decides a file isn't worth reading, it is not weighing evidence about
that file — it is guessing from the file's name and its position in the tree. And that
judgement is *invisible*: nothing records "I chose not to look at forty files", so
neither party can audit it.

The field does not make the agent read more. It makes **not-reading evidence-based
instead of a guess.** A skipped cold region becomes a defensible decision; a skipped
hot one becomes a catchable mistake.

### The second-order benefit: holding a whole system at once

An agent working at file granularity holds one slice deep and the rest not at all —
it can only ever reason about *the file it is in*. Thirty magnitudes can be held
entirely, alongside the work.

That is a different kind of thinking. Proportion, relationship and priority only exist
at that level. *"This function has a bug"* and *"three quarters of the pressure in this
project lives in one subsystem"* are not the same observation, and only the second one
changes what you decide to do.

---

## 3. Four principles

Everything else in this document follows from these.

### 3.1 State is a function of time, not the result of a simulation

A simulation must be *executed* to know where it is. A function has a value whether or
not anyone evaluates it.

Every stored quantity is a pair — a value and the time it was last touched — decayed
on read:

```
value_now = value_stored × e^(−λ · (now − t_stored))
```

The decay already happened. Nothing computed it. There is no tick, no scheduler, no
thread, no resident process. The field's value at 3am on a Sunday is well-defined even
though the machine was asleep.

Folding a new event is O(1) regardless of how much history preceded it:

```
A ← A × e^(−λ·(t_now − t)) + m
t ← t_now
```

Between events, the work performed is exactly zero.

### 3.2 Sensors are parasitic, never active

The cost of a small operation is almost never the operation — it is *reaching* the
data. Process start, file open, cache miss, parse. Touching a number already in a
register is thousands of times cheaper than fetching the same number cold.

Therefore a sensor may only report values the host **has already produced**, at a
moment the host was **already running**.

> **Never scan for the map. Only attach to a scan that was already going to happen.**

### 3.3 Semantics are deferred entirely to the consumer

No component of the pipeline ever produces prose, a description, a classification or
a judgement about code. Sensors emit numbers. The store holds numbers. The serving
layer emits numbers and paths.

Anything that would require understanding is out of scope by construction.

### 3.4 No operation may be O(anything that grows)

O(1) per event. O(groups) per read. Never O(history), never O(pixels × time).

If a proposed feature cannot be expressed within that bound, it is the diffusion
problem wearing a new hat.

---

## 4. Sensors

### 4.1 Where sensors can exist

Principle 3.2 answers this. A sensor cannot be placed wherever you like — it can only
go **where the system already renders a verdict.** Every such point is a free sensor,
and nowhere else is.

| Source | Verdict it already renders |
|---|---|
| Compiler | warnings, errors — a verdict on code |
| Test runner | pass/fail/duration — a verdict on behaviour |
| Linter | finding + severity — a verdict on style/correctness |
| Git | which files were touched — a verdict on attention |
| Git commit messages | "fix" — a verdict that something *was* broken |
| Package manager | versions behind — a verdict on currency |
| Ester | structured findings — a verdict on quality, already produced |
| The agent itself | edits, retries, claims of completion — a verdict on difficulty |

Designing the sensor set is therefore not invention. It is a **survey**: walk the
toolchain, list every place a verdict already falls out, stop.

### 4.2 Three kinds of statement

The 1.0 draft treated every signal as pressure that accumulates. Three different
things actually arrive, and they behave differently. Collapsing them is a real bug.

| Type | Example | Behaviour |
|---|---|---|
| **State** | dependency is 3 versions behind | **Overwrite.** True or not; accumulating is meaningless |
| **Event** | build failed, commit landed | **Accumulate + decay.** Recurrence is the signal |
| **Judgment** | Ester finding, lint warning | **Accumulate, and must be able to clear** |

State signals do not belong in the accumulator at all. They belong in a plain
current-facts slot beside it.

### 4.3 The sensor spec sheet

Seven questions, answered for every sensor. This is what turns sensor design from an
abstraction into a form.

1. **Tap** — which already-existing verdict does it read?
2. **Type** — state, event, or judgment?
3. **Key** — what path does it attach to?
4. **Denominator** — raw, per-time, or per-KLOC?
5. **Channel** — pressure, health, or activity?
6. **Clears when** — what makes it go away?
7. **Cost** — is it genuinely free, per 3.2?

**Question 6 is the one that gets forgotten and it is the one that kills the tool.**
If nothing clears, everything eventually turns red and the field becomes decoration.

**Question 4 is the frequency-bias guard.** Without a denominator, large files and
busy modules always look worst, and the field just re-reports size.

### 4.4 Worked examples

| Sensor | Type | Key | Denom | Ch | Clears when |
|---|---|---|---|---|---|
| Ester finding | judgment | file:line | per KLOC | R | absent from next run |
| Commit churn | event | touched files | per 30d | B | decays with time |
| Dependency staleness | state | manifest → module | raw | R | version catches up |
| Test failure | event | test → source file | per run | G | passes again |
| Build warning | judgment | file | per KLOC | R | absent from next build |
| Agent edit-retry | event | file | per session | B | decays with time |

**Five to eight sensors is likely the whole useful set.** Past that, effort goes into
tuning normalisations against each other rather than adding information — and see
§11 on calibration mattering more than coverage.

---

## 5. Routing

Nearly every verdict in §4.1 already carries the same identifier: **a file path.**

> **Route everything by file path. Aggregate upward.**

That is the entire routing layer. The region map is not a hand-drawn geography; it is a
small `path → group` configuration, most of which is *derivable from the directory
tree*. Only two things need authoring by hand:

- exceptions (a file that belongs somewhere other than where it sits)
- semantic groupings that cut across directories

This is what makes the front-loaded work **finite**. It is also why cross-project
comparison comes free rather than being a separate feature — every project speaks the
same key.

Hierarchy: `file → group → subregion → project → fleet`. Values aggregate upward;
queries descend.

---

## 6. State, and the vortex

### 6.1 The vortex

Signals arrive at full resolution and spiral inward, merging into progressively
coarser rings as they age. Nothing is deleted by rule; **resolution is lost by the
geometry itself.**

| Ring | Span | Resolution |
|---|---|---|
| 0 | last hour | every event |
| 1 | last day | hourly |
| 2 | last week | daily |
| 3 | last month | weekly |
| 4 | last year | monthly |

Each fold is one addition, and folds occur geometrically rarely. The critical
property:

> **Total work per datum is constant regardless of how long the system runs.**

A signal from three years ago cost the same total processing as one from this morning
— a handful of merges — because each fold halved the number of survivors. The mass is
bounded: the structure holds ~5 numbers per channel whether it has run for a day or a
decade. It does not grow; it only gets less precise toward the centre.

This is the shape behind exponential histograms, round-robin databases and LSM trees.
Well-trodden, not speculative.

### 6.2 What the rings buy

Not just compression — they hand back the dimension 1.0 threw away by refusing
timestamps. Temporal profile is readable directly from the ring gradient:

| Profile | Reading |
|---|---|
| Hot at rim, cold inside | **Spike.** An incident. Something happened yesterday |
| Hot all the way through | **Structural.** Wrong for months, nobody fixed it. Debt |
| Cooling from inside out | **Resolved.** Leave it alone |
| Warming toward the rim | **Degrading.** The one worth catching |

The last one cannot be obtained from an instantaneous value at any price. A region
that has been quietly red for six months is a completely different recommendation from
one that went red this morning — and five floats tell them apart.

### 6.3 Cost

**Measured 2026-08-13 (Stage 1, CPython 3.14). The predictions below were wrong on
size and pessimistic on speed — corrected here; see `results.md`.**

For a six-repo fleet at ~50 groups per project (~300 groups):

| Item | Predicted | **Measured** |
|---|---|---|
| 300 groups | ~18 KB | **227 KB resident, 303 KB on disk** |
| At the 4096-group ceiling | ~250 KB | **2.0 MB** |
| Real fleet, 40 groups, 2.9k signals | — | **17 KB on disk** |

The prediction counted five float32 rings per channel and nothing else. It omitted
64-bit floats, the per-channel timestamp, and — dominating everything — the bounded
top-k pointer dict, which is ~75% of resident size at ~580 B/group. That memory is not
waste: the pointers are what make Layer 2 possible at all, and Layer 2 is a safety
requirement (§8.3), not a convenience. **Decision: accept the cost, correct the claim.**
227 KB is still cache-resident and still nothing.

| Operation | Predicted | **Measured** |
|---|---|---|
| Ingest one signal | ~50 ns | **889 ns** (CPython; 5 multiply-adds + dict) |
| Fold all 300 groups | ~100 µs | **287 µs** |
| Fold 4096 groups | — | **3.9 ms** |
| Serve Layer 1, fleet-wide | — | **0.08 ms, 32 tokens** |
| Serve Layer 2 for one group | — | **0.03 ms, 81 tokens** |

Ingest is 18× the predicted figure and it does not matter in the slightest: at the
fleet's *measured* signal volume of ~16 git signals/day (§6.5), one day of ingest costs
**14 microseconds**. The cost model was solving a problem the workload does not have.

Opening the field costs well under a millisecond. Nothing runs between looks.

### 6.4 Which rings actually carry information

Measured, not assumed. Five rings are stored; **three are classified on.** The fleet's
real signal volume, from 180 days of git history across eight repos: **505 commits,
2,853 file-touches, ~16 signals/day**, busiest single day 42 commits.

| Ring | Half-life | Status |
|---|---|---|
| hour | 1 h | **Stored, not read.** At ~16 signals/day the event interval exceeds the half-life, so the ring is a sawtooth. Measured at 1.37× the true rate on a stream it should report exactly. It reports sampling phase, not load. |
| day / week / month | 1 d / 7 d / 30 d | **The working range.** Agree to within 1.4% on a constant stream. |
| year | 365 d | **Stored, not read.** Needs 2–3 half-lives to saturate — still reading 0.86× low after three simulated years. Classifying on it would make every steady workload look like it is warming for the system's first few years. |

This is a cold-start property, not a bug, and it is the kind of thing that would have
been invisible without building it. The two excluded rings cost two floats each and are
kept because they become meaningful at other volumes — a busier fleet would make the
hour ring live, and a long-running install eventually saturates the year ring.

### 6.5 Optional read-time coupling

If the "related things light up together" effect from 1.0 §17 is wanted, it does not
require a simulation. Compute it on read:

```
displayed_i = own_i + Σ w_ij · own_j
```

One sparse pass over the groups. **One step of influence on demand**, rather than
infinite steps computed continuously. Visually near-identical, computationally not
comparable.

---

## 7. Channels

Three channels, mapped to colour for the human view.

| Channel | Meaning | Fed by |
|---|---|---|
| **R — pressure** | something is wrong or accumulating | findings, warnings, failures, staleness |
| **G — health** | evidence things are working | tests present and passing, clean builds |
| **B — activity** | attention is being spent here | churn, edit recency, agent sessions |

Combinations carry most of the information:

| Reading | Interpretation |
|---|---|
| Red, no blue | **Broken and abandoned.** Highest value — nobody is on it |
| Red + blue | Actively being fought. Known, in hand |
| Green, no blue | Healthy and stable. Leave alone |
| Dim everywhere | Dead code, or unsensored territory |

---

## 8. Serving

### 8.1 Two layers, pushed and pulled

The split is not convenience. It is what makes the top layer **unconditional** —
cheap enough to be handed over always, for the whole fleet, without anyone deciding it
is warranted. Merge the layers and you either pay the denser cost fleet-wide or lose
the always-on property. Always-on is the entire point, because the failure mode being
addressed is *not knowing to look.*

**Layer 1 — temperature. Pushed, always, unconditionally.**

Magnitude and direction per region. **No nouns.** Semantics-free by construction.
Order of 50–100 tokens for a whole fleet. Answers only: *is anything happening, and
roughly where.*

**Layer 2 — the shape of the heat. Pulled, only for regions Layer 1 flagged.**

Still purely metadata about the *signal*, never about the code:

- **which channel** — pressure, health, or activity
- **temporal profile** — spike / sustained / cooling / warming (§6.2)
- **driving sensor** — which tap contributed most. One word, no interpretation
- **concentration** — see §8.2
- **pointers** — a handful of paths, no descriptions

Order of 200–400 tokens for one project.

**Layer 3 — the investigator's own tools.** Full fidelity, on one region, using
normal reading. Not part of this system.

Each layer is a strict refinement, not a summary of the one above. Nothing is
duplicated between them, which is what keeps all of them light.

### 8.2 Concentration

One file screaming and twenty files each mildly warm produce the *same regional
magnitude* and mean opposite things.

| Shape | Reading |
|---|---|
| Concentrated | A specific bug. Go fix that file |
| Diffuse | Structural. The design is wrong; fixing any one file won't help |

Cost: a count of distinct contributing keys. It is the difference between proposing a
patch and proposing a refactor.

### 8.3 Why Layer 2 is a safety requirement, not a luxury

A bare magnitude is **dangerous to act on.** It looks authoritative, carries no caveat,
and will happily point at a region that is hot for a boring reason. If Layer 1 were all
there was, a reader would over-trust it — and confidently-wrong is worse than
uninformed.

Layer 2 is what makes the pointing safe: it lets the reader distinguish *worth your
time* from *loud but trivial* before committing to a direction.

### 8.4 Cold is the larger half

Heat says where to go. **Cold says where you do not have to** — and that is a far
larger area.

Without the field, absence of information means nothing: "nothing is wrong there" is
indistinguishable from "nobody looked." With trustworthy sensors, silence becomes a
*positive statement*. That converts most of the fleet into territory that can be
dismissed without checking, and dismissing is where the reader's budget actually goes.

This is entirely contingent on sensor trust. See §11.

### 8.5 The human view

The grid render — the heatmap proper. On demand only, from the same store, one
direction:

```
signals → store → { agent reads L1/L2 ranked;  human reads rendered grid }
```

The table is the record; the grid is an interface. Neither pretends to be the other.
Rendering is a projection of ~300 group values into a raster — the fine grain lives in
the *rendering*, not in the state. There is no micro-information to process.

Note that §3.3 partly vindicates the 1.0 instinct here: as a **pointer**, colour is
exactly the right encoding, because magnitude-without-meaning is precisely what is
being communicated and there is no content to lose. The 1.0 error was making colour
the *record*.

---

## 9. Invariants

The rules that keep the system honest. A change that violates one of these is a
different system.

1. **No operation may be O(anything that grows.)**
2. **No timers. No resident process.** Work happens only when someone else's work is
   already happening. What shows up in a task manager is residency, not cycles.
3. **No summarisation anywhere in the pipeline.** Numbers and paths only.
4. **Never scan for the field.** Attach to scans that already happen.
5. **Every sensor declares a clearing condition.**
6. **Every sensor declares a denominator.**
7. **Semantics are deferred entirely to the consumer.**
8. **State is a function of time.** If it requires stepping, it is wrong.

---

## 10. What was removed from 1.0, and why

| Removed | Reason |
|---|---|
| Diffusion / propagation (§10, §17, §36–37) | The sole O(pixels × time) term — the entire cost problem. Replaced by optional read-time coupling (§6.4) |
| Sequencer, work budgets, stale-event generation counters (§§19–21, 24) | Existed *only* to keep a continuous field from eating the machine. No field, no problem |
| Ban on timestamps and history (§10) | Trend is the single most valuable dimension; the ban forbade it. Replaced by the vortex (§6) |
| Grid as the record (§4–5) | The grid is a render. The store is the record |
| 1.5M-pixel state | Pixels are a projection of ~300 numbers, not entities |
| "Analog computation" / emergent-substrate framing (§§29–30) | See §12 |
| Adaptive geography | Still deferred, per 1.0 §34 — but now because routing is derived from paths, not because adaptation is hard |

Retained from 1.0, largely intact: the region/subregion/group hierarchy, the RGB
channel encoding, the one-way sensory architecture (§8.2–8.3), competing states over
predefined good/bad (§12), the failure-mode catalogue (§32), and *"a beautiful field is
not success"* (§32.7) — which should be read as the project's motto.

---

## 11. Known risks and honest limits

**The sensor set is the whole ballgame, and it is unproven.** Everything in this
document is mechanism, and mechanism is the easy part. The value depends entirely on
whether five to eight sensors produce a ranking that beats *"the files touched most
recently."* That baseline is embarrassingly strong; defect-prediction research is
largely a graveyard of methods that failed to beat churn. Unknown until measured —
see `experiments.md` E0.

**Calibration matters more than coverage.** The savings only materialise if the reader
trusts the field enough *not to verify it*. A field that is right 70% of the time gets
checked every time, and checking costs what searching cost. A narrow field with five
trusted sensors beats a broad one with twenty that is occasionally wrong. Do not
optimise coverage first.

**Decoration is the most likely death.** Dashboards get built, admired, then not
opened. The structural defence is that the primary reader must be the *agent*, during
work it is already doing — if value requires a human deciding to go look at a picture,
it will erode.

**A rule-based field reveals what it was configured to reveal.** Surprises come from
configured *combinations*, not from unanticipated phenomena. That is a real ceiling on
the "tells me things I didn't know" ambition.

**Smoulder yes, ignition no.** Slow things announce themselves — accumulating findings,
drifting dependencies, a test flaky for weeks, churn concentrating. Sudden breaks do
not; a clean codebase can be broken in one commit with no prior warmth. The
compensating fact is that smoulder is most of what actually costs time.

**It will not fix** over-reading once in the right place, context loss across
compaction, or genuinely novel work that isn't *located* anywhere.

---

## 12. On "analog", and the map-mind

Worth stating precisely, because the intuition is right and the vocabulary invites
overclaiming.

**What is genuinely analog-like:** the state is *continuous in time and always
defined*. It is not updated; it *is*. Decay is not bookkeeping performed on a
schedule — it is a closed-form property of the representation, true at every instant
including instants nobody observed. Nothing steps. Nothing ticks. The field has a
value at every moment of its existence, the way a physical quantity does, and reading
it is measurement rather than computation.

That is a real property, it is unusual in software, and it is the source of the
system's lightness. It is what the "it should just *be*" instinct was reaching for.

**What is not:** it is discrete, deterministic, and rule-based. Nothing emerges.
There is no computation happening in the field, no dynamics, no self-organisation, and
no meaning that was not put there by a sensor. Colour is an encoding, not a state of
matter.

The honest description of the whole thing is a **map-mind in the cartographic sense**:
a persistent, cheap, pre-semantic spatial memory of a system, which knows *that* and
*where* but never *what* — and whose entire usefulness comes from handing the *what*
to something that can actually think.

---

## 13. Status and applications

Design settled. Nothing built. Nothing should be built before `experiments.md` E0 and
E6 return, because those two use git history alone and can falsify the premise without
any instrumentation existing.

Applications are adjustable per use case; the mechanism does not change:

- **Agent oversight** (primary target) — fleet-level attention field for Claude
  working across six repos
- **Human oversight** — the rendered grid, for the fleet owner
- **LLM training** — the original 1.0 motivation; unexamined in this revision and
  should be treated as a separate design question rather than assumed to follow

---

*Companion document: `experiments.md`.*
