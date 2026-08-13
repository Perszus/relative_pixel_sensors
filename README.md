# Relative Pixel Sensors

*by Roberts Kains ([@Perszus](https://github.com/Perszus)) · Apache-2.0*

**A nervous system for a machine full of software.**

It answers *whether* something wants your attention, so that finding out *what* —
the expensive question — only gets paid for where it is warranted.

It is not a linter, a dashboard, or a CI system, and it runs no analysis of its
own. It taps verdicts other tools already produce as a by-product — git's index,
a test runner's cache, a reviewer's report, the filesystem's own shape — and
accumulates them into a small decaying field over regions of whatever it is
pointed at.

```
RELATIVITY PIXELS — fleet field
2026-08-13 14:59 · 18 projects · 139 regions · 34 stalled · 14 in hand · 76 quiet

STALLED  — pressure with nobody on it — look here first
  app/lib/services      R  13.0 B   0.0 /9   = 1.4  cooling   3 files with findings
  machine               R  30.5 B   0.0 /0   =  —   standing  C: is 0.5% free

PATTERNS  — only visible with the whole fleet in one frame
  · engine.cpp and Bridge.kt changed together 27x — coupling the import graph cannot see
  · load-bearing and under pressure: app/lib/services (4 regions depend on it)
  · never reviewed: three projects — absence of findings there means absence of looking
```

## What it is aiming at

Most tools tell you what is wrong once you have already decided where to look.
The expensive part of working in an unfamiliar system is not diagnosis, it is
**orientation** — deciding where to point a microscope. This is the telescope
for that: the whole system at low magnification, organised by what you can *do*
about each part.

Three properties it is built to keep:

**Always on, never running.** State is a function of time rather than a
simulation of it. Every value is stored with a timestamp and decayed when read,
so nothing ticks, nothing is scheduled, and no process is resident. Absorbing a
signal is five multiply-adds; reading the whole system is under a millisecond.
Between passes the field is still current, because there is nothing it needs to
be doing.

**Parasitic.** It attaches to a host, runs on machinery the host was already
running, and costs it almost nothing. Nine of its eleven probe kinds never
execute anything. What it must never be is transmissible in the other sense: it
is trivial to attach and it does not attach itself.

**It grows onto what it finds.** There is no atlas of where things live. A
directory holding `Cargo.toml` is a Rust crate wherever it sits, and rules latch
onto *what a thing is* rather than where it is. Point it at anything.

## How it works

| layer | what it is |
|---|---|
| **Recognizer** | a structural signature: `Cargo.toml` present → rust-crate |
| **Probe** | a way to extract one value — presence, count, regex, parse, size, host state |
| **Rule** | declarative: a scope, a probe, a channel and a weight |
| **Field** | decaying accumulators over regions, in three channels |
| **Serving** | one line, a briefing, or a colour grid |

Rules are data, so adding one is a line rather than a function. Scoping is what
makes volume safe: a rule latched to `rust-crate` is silent on everything that
is not one, and on a typical run about a third of the rule table fires on
nothing at all — which for a rule that detects committed credentials is the
correct and desired answer.

The channels are **R** pressure, **G** health, **B** activity, and the reading
that matters is the combination. Red without blue is damage nobody is working
on. Red with blue is already in hand. The same magnitude, opposite instructions.

See [`PROBES.md`](PROBES.md) for the probe taxonomy — eleven kinds, classified
by what each needs in order to answer, because that is what predicts cost,
whether it stays parasitic, and how it fails.

## Using it

```
python collect.py              absorb, then print the one-line field
python collect.py --read       read without absorbing
python collect.py --sensors    the sensor spec sheet
python collect.py --rediscover re-scan for subjects
python ruleset.py              the rule table
python ruleset.py --live       which rules fire, and why the silent ones are silent
python audit.py                the field's claims vs independently derived facts
python explain.py <subject>    why the field says what it says
python explain.py --rule <id>  one rule, re-evaluated everywhere it applies
python rpwrap.py test -- <cmd> record what a command did, so the field can read it
python -m rp.remote --refresh  fetch the advisory snapshot
```

### Checking a finding

Every finding can account for itself. `explain.py` names the rule, the probe it
called, what that probe returns **when run again right now**, and the one thing
that would falsify it:

```
huts
  R  magnitude 45.48
         8.00  rust-unwrap
                probe    content('*.rs', '\.unwrap\(\)')
                says     unwrap() calls that can panic
                now      335  (x weight 0.05, capped at 8)
                false if no line matched /\.unwrap\(\)/ in `*.rs`
```

This exists because a confidently wrong reading is only survivable if checking
it is cheap. One was published here — a passing test suite reported as broken,
on the strength of a stray cache — and establishing the truth took twenty
minutes of manual digging because nothing pointed at the evidence underneath.

Point it somewhere by setting `RP_ROOTS` (path-separated) or writing
`roots.json`:

```json
{ "roots": ["/home/you/code", "/srv"] }
```

With neither, it looks in the directory containing the tool. `BRIEF.txt` is the
wide-field view, rewritten every pass. `view.json` is the same field rendered
for a viewer.

## Design rules

**Tap verdicts, never scan.** If a signal is not already being produced by
something else, it does not get collected.

**Declare the statement type.** An *event* accumulates and decays; a *standing*
statement is set and must clear; a *state* replaces the last answer. Collapsing
them is what kills this kind of tool — a judgement that never clears saturates
the field within weeks.

**Answer for the whole subject at once.** Clearing is the half that gets
forgotten. A sensor can only retract a finding it no longer sees if it reports
its complete picture every pass.

**A verdict older than the code is not a verdict.** A test run or a review is an
opinion about one snapshot; once the code moves past it, the opinion is not
wrong so much as about something else.

**Absence must be distinguishable from silence.** This field reads quiet as
licence to skip, so a probe that cannot tell "looked and found nothing" from
"did not look" manufactures false negatives.

**Channels do not share a scale.** One commit touches thirty files; one finding
is one finding. Bands are calibrated per channel rather than forcing weights to
agree, because making them agree would mean falsifying what each sensor
measures.

## Status

Honest about what is and is not established.

**Tested:** the mechanism. Lazy decay is exact to 2.7e-11, ingest is O(1) in
history, reads are O(regions), idle cost is structurally zero, and resident
state is a couple of hundred kilobytes. `python run_experiments.py` runs the
benchmarks; `python -m pytest` runs the invariant suite; `audit.py` re-derives
the field's claims by a different code path and has caught two real bugs.

**Not established:** that the field ranks better than "what changed most
recently". That needs a replay over history longer than the author's own fleet
has, and it is the gate the whole idea stands or falls on. The weights are
documented guesses — `calibrate.py` measures what each sensor contributes, and
nothing tunes them, because tuning without ground truth is moving numbers until
the output looks agreeable.

## Licence and attribution

Apache-2.0. Copyright 2026 Roberts Kains.

If you redistribute this or a derivative, §4(d) asks you to carry the [`NOTICE`](NOTICE)
file along with it — that is the one line of credit this project asks for. If you
use it in research or writing, [`CITATION.cff`](CITATION.cff) gives GitHub's
"Cite this repository" button something to hand you.

Contributions welcome, particularly rules and probe kinds. A rule is one line in
[`rp/rules.py`](rp/rules.py); a probe kind is a section of [`PROBES.md`](PROBES.md)
that does not exist yet.
