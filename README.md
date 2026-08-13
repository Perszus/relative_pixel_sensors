# Relative Pixel Sensors

A hyperlight attention field over a fleet of repositories. It answers **whether**
something wants attention, so that finding out **what** — the expensive question —
only gets paid for where it is warranted.

It is not a linter, a dashboard, or a CI system. It runs no analysis of its own. It
taps verdicts that other tools already produce as a by-product — git's index, git's
log, a reviewer's report, a test runner's cache — and accumulates them into a small
decaying field over regions of the codebase.

## The idea in one paragraph

State is a **function of time**, not a simulation of it. Every value is stored with a
timestamp and decayed when read, so nothing ticks, nothing is scheduled, and no
process is resident. Absorbing a signal is five multiply-adds. Reading the whole
fleet is under a millisecond. Between passes the field is still current, because
there is nothing it needs to be doing.

## Reading it

    python collect.py            absorb, then print the one-line field
    python collect.py --read     read without absorbing
    python collect.py --sensors  print the sensor spec sheet

`BRIEF.txt` is the wide-field view — the whole fleet on one screen, rewritten every
pass. It is organised by what a reader can *do*:

- **STALLED** — pressure with nobody on it. Look here first.
- **IN HAND** — loud, but someone is already working it.
- **PROJECTS** — shape: language, size, tests, docs, last commit.
- **PATTERNS** — statements only true of the whole fleet at once.
- **QUIET** — regions with nothing to say. Listed by name, because knowing what to
  skip is the larger half of the payload.

`view.json` is the same field rendered for a viewer. `field.json` is the store.

## Design rules the sensors obey

**Tap verdicts, never scan.** Nothing walks the tree for the field's benefit. If a
signal is not already being produced by something else, it does not get collected.

**Declare the statement type.** An *event* accumulates and decays; a *standing*
statement is set and must clear; a *state* is the current answer and replaces the
last. Collapsing the three is the bug that kills this kind of tool — a judgment that
never clears saturates the field within weeks.

**Answer for the whole fleet at once.** Clearing is the half that gets forgotten. A
sensor can only retract a finding it no longer sees if it reports its complete
picture every pass.

**Pressure is filtered to what is yours; activity is not.** A repair inside a
vendored library is nobody's job here. A vendor bump is still real work.

**A verdict older than the code is not a verdict.** A test run or a review is an
opinion about one snapshot; once the code moves past it, the opinion is not wrong so
much as about something else.

**Channels do not share a scale.** One commit touches thirty files while one finding
is one finding. Bands are calibrated per channel rather than forcing the weights to
agree, because making them agree would mean falsifying what each sensor measures.

## Status

The mechanism is tested (`run_experiments.py`, results in `results.md`): lazy decay
is exact, ingest is O(1) in history, reads are O(regions), and idle cost is
structurally zero. What has **not** been established is whether the field ranks
better than "what changed most recently" — that needs history this fleet does not
have yet, and it is the gate the whole idea stands or falls on. See `experiments.md`.

Weights are documented guesses. `calibrate.py` prints what each sensor actually
contributes, which is the honest way to set them.

## Companion

Sentinel (a separate project) renders this field as a colour grid, one pixel per
region, and drives an absorption pass while it runs. Nothing here depends on it.
