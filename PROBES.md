# Probe kinds

## What this is

A nervous system for whatever it is attached to. Probes are nerve endings:
dense, dumb, and specialised by *what they are sensitive to* rather than by
where they sit. An ending does not interpret. It fires, and interpretation
happens somewhere else — which is the same separation this field has always
had between the gradient and the reader.

Three consequences that are design constraints, not decoration:

**Innervation is a growth rule, not a map.** There is no nerve atlas per body;
there is a rule that grows nerves wherever tissue is. A hardcoded root list is
an atlas. Attachment has to discover the host's anatomy — volumes, code trees,
toolchains, services — and innervate what it finds. What is known in advance is
*structure*: what a Rust crate is, how a log rotates, where a package manager
keeps its cache, how these things typically fail. That prior anatomy is the rule
library. It is knowledge of structures, never of a particular machine.

**Reflexes do not go to the brain.** Some signals arc at the spine because the
response is unambiguous and the delay is the danger: committed credentials, a
conflict marker in a shipped file, a system drive at 0.5%. Those must surface
immediately and unranked. Everything else is integrated, weighed against the
rest of the field, and reported as gradient. Collapsing the two tiers means
either burying an emergency in a ranking or promoting every finding to one.

**Parasitic in the precise sense.** It attaches to a host, runs on machinery the
host was already running, costs it almost nothing, and returns benefit. What it
must never be is transmissible in the other sense: it is trivial to attach and
it does not attach itself. Nothing here installs, spreads, or persists without
being asked.

Where the metaphor stops: a body has a fixed plan and software does not.
"We already know the patterns" holds for structures and their pressure points;
it does not license encoding one house's conventions as anatomy. That is the
`unearned norms` failure under EXPECTATION below, and it is how a section
teaches a reader to skip it.

---

## The taxonomy

A rule is data. A probe is the thing a rule names to get a number. The rule
table can only ask questions its probes can answer, so the probe taxonomy — not
the rule count — is what actually bounds this system.

Probes are classified by **what they need in order to answer**, because that is
what predicts their cost, whether they can stay parasitic, and how they fail.
Two probes that read the same file are different kinds if one needs a parser
and the other needs a timestamp.

Ordered by what they require, cheapest first.

---

## 1. STAT — filesystem metadata only

**Needs:** a path. No file contents are read.
**Cost:** microseconds. **Parasitic:** yes. **Deterministic:** yes.

The cheapest possible question, and a surprising amount reduces to it. Presence,
absence, size, count, modification time, permissions, whether something is a
link, whether two paths are the same inode.

*Niche probes:* file exists · directory populated · count matching glob · newest
mtime · oldest mtime · total bytes · file over N bytes · executable bit set ·
symlink target · empty file · duplicate path across subjects.

**Failure mode:** silent scope errors. A glob that matches nothing looks
identical to a directory that is genuinely empty. Every stat probe needs to
distinguish *absent* from *not looked at*, or it manufactures false negatives —
which this system reads as safety.

---

## 2. TEXT — contents as characters

**Needs:** to read the bytes. No grammar.
**Cost:** milliseconds; index-backed grep keeps it near stat.
**Parasitic:** yes. **Deterministic:** yes.

Regex and literal search. This is where most cheap rules live, and where most
false positives live too: text has no idea what it is looking at, so a variable
named `password` and a password are the same to it.

*Niche probes:* regex hit count · matching files · first match with line number ·
match density per KB · binary signature at offset · encoding detection · line
count · longest line · trailing whitespace · mixed line endings · non-ASCII in
identifiers · entropy of matched literal.

**Failure mode:** confident nonsense. The first secrets rule here was 100% false
positives across six repos because `token = credentials.credentials` matches a
pattern for `token = "..."`. Text probes need a second filter — entropy, a
placeholder list, a negative pattern — before their output is trustworthy.

---

## 3. GRAMMAR — contents parsed as a language

**Needs:** a parser per language.
**Cost:** tens of milliseconds per file. **Parasitic:** yes.
**Deterministic:** yes.

The jump text cannot make. A regex can count the word `function`; only a parser
can say a function is 400 lines long, nested nine deep, takes eleven arguments,
or is never called. Python has `ast` in the stdlib, which makes that ecosystem
free; others need a grammar or a tolerant heuristic parser.

*Niche probes:* function length distribution · max nesting depth · cyclomatic
complexity · parameter count · class size · dead private functions · duplicated
blocks · import graph (exact, not regex) · unreachable branches · TODO attached
to a specific function · public API surface count · type annotation coverage ·
mutable default arguments · bare raise · shadowed builtins.

**Failure mode:** brittleness and silence. A parser that fails on one syntax
version returns nothing, and nothing is indistinguishable from clean. Grammar
probes must report *parse failure* as its own outcome, never as zero.

---

## 4. HISTORY — a temporal record

**Needs:** a log: VCS, an append-only file, or this field's own rings.
**Cost:** seconds if unbounded; cacheable on a revision id.
**Parasitic:** yes. **Deterministic:** yes, given the same cut.

Everything about change rather than state. This is the only kind that can
answer "is this getting worse", which no snapshot can.

*Niche probes:* commits touching a path · churn per file · code age · time since
last change · revert frequency · time between a bug's introduction and its fix ·
files that appear only in fix commits · commit size distribution · co-change
coupling · contributor count · how long a TODO has survived · whether a file is
in its first week of life · how long a thing has been broken.

**Failure mode:** the horizon. Answers change meaning with the window — "10
commits" over a week and over a year are different facts — so a history probe
that does not state its window produces numbers nobody can compare.

---

## 5. GRAPH — relations across subjects

**Needs:** two or more subjects and a join key.
**Cost:** O(edges); cheap once the inputs exist.
**Parasitic:** yes. **Deterministic:** yes.

The kind that can only exist because everything is in one field. Nothing here is
answerable from inside a single project.

*Niche probes:* fan-in / fan-out · depth from an entry point · cycles · orphaned
modules · the same dependency pinned to different versions in two projects · a
project depending on another project in the fleet · duplicated file content
across repos · fork divergence · a shared file that has drifted between forks ·
transitive blast radius of a change · which subjects share a toolchain.

**Failure mode:** false edges. A guessed relation is a structural claim not
present in the code, and it is worse than a missing one — it sends a reader
somewhere the system never actually connects. Resolve or return nothing; never
approximate.

---

## 6. VERDICT — another tool's conclusion

**Needs:** the other tool to have already run and written something down.
**Cost:** parsing a file. **Parasitic:** completely — this is the ideal kind.
**Deterministic:** yes.

Test caches, linter reports, compiler diagnostics, coverage files, review
ledgers, CI status, SBOMs, advisory databases. The tool did the hard work; this
reads the receipt.

*Niche probes:* failing tests from a runner cache · coverage percentage ·
lint diagnostics by severity · compiler warning count · open findings from a
review · suppressed warnings · benchmark results · flaky test list · CI last
status · packaged artifact size.

**Failure mode:** staleness, and it is the most dangerous failure in the system
because the output looks perfectly current. A verdict is an opinion about one
snapshot; once the code moves past it, the opinion is not wrong but *about
something else*. Every verdict probe must carry the age of its evidence, and a
verdict older than the code must be reported as unknown rather than as pass.

---

## 7. EXPECTATION — comparison against a norm

**Needs:** a model of what *should* be there.
**Cost:** free once the observation exists. **Parasitic:** yes.
**Deterministic:** yes.

A different epistemic act from the others: "no tests" is not an observation, it
is an inference from an expectation. That expectation has to come from
somewhere — a recognizer (a Rust crate should have a lockfile), a sibling (this
fork has a CI file and its twin does not), or a declaration (the manifest says
1.4.0 and the tag says 1.3.2).

*Niche probes:* declared-vs-resolved dependency drift · version disagreement
between manifest, tag and changelog · imported but undeclared package ·
declared but unimported package · convention present in siblings and missing
here · config key documented but unread · a file every peer has and this one
does not.

**Failure mode:** unearned norms. An expectation nobody agreed to produces
findings that are technically true and entirely unwanted, and a reader learns to
skip the whole section. Expectations must be derived from the subject's own kind
or its peers, never from a house style.

---

## 8. AMBIENT — host state, attached to no subject

**Needs:** the machine. **Cost:** one syscall or one command.
**Parasitic:** yes. **Deterministic:** no — it changes second to second.

The environment everything else sits in. Not a property of any project, so it
belongs to a subject of its own.

*Niche probes:* free space per volume · memory pressure · VRAM in use · CPU
load · process running · port listening · service state · uptime · battery ·
thermal state · network reachable · scheduled task registered · time since last
reboot · which model is resident in an inference server.

**Failure mode:** it is a level, not an event. Ambient values are true *now* and
meaningless as history unless deliberately sampled, so they must never be
accumulated like events — a disk that was full an hour ago and is fine now
should read as fine.

---

## 9. IDENTITY — authoritative facts about an artifact

**Needs:** hashing, signature verification, or a package registry.
**Cost:** hashing is IO-bound; verification is fast.
**Parasitic:** yes. **Deterministic:** yes.

What a thing actually *is*, independently of where it sits or what it is called.
A path is not an identity.

*Niche probes:* content hash · signature valid · publisher · certificate expiry ·
known-good hash match · licence of a dependency · package version resolved ·
the same binary present under two names · an artifact newer than its source.

**Failure mode:** trust transfer. A valid signature says who signed something,
not whether it is safe, and reporting one as the other is worse than silence.

---

## 10. EXECUTION — run it and see

**Needs:** to actually run something.
**Cost:** seconds to minutes. **Parasitic: NO.** **Deterministic:** no.

Does it build, does it start, does it pass, how fast, what does it log. The only
kind that can answer whether something *works*, and the only kind that violates
the founding rule.

*Niche probes:* build exit code · test suite result · startup time · smoke
request · memory high-water mark · log error rate · crash count.

**Failure mode:** two of them. It costs real time, so it cannot run on every
pass; and it fails *silently in the wrong direction* — a wrapper that stops
being invoked reports no failures, which this system reads as health. If
execution is ever added it must be through wrappers on invocations that were
happening anyway, and absence of a recent result must read as unknown.

---

## 11. REMOTE — something off this machine

**Needs:** network. **Cost:** unbounded, unreliable.
**Parasitic:** no. **Deterministic:** no.

Advisory databases, registry latest-versions, upstream branch state, uptime
checks.

**Failure mode:** it makes the field's availability depend on someone else's,
and a probe that times out returns zero, which reads as clean. Any remote probe
must cache aggressively and distinguish *checked and fine* from *could not
check* — the second is not a pass.

---

## What this bounds

| kind | parasitic | deterministic | can answer "is it getting worse" | built |
|---|---|---|---|---|
| stat | ✓ | ✓ | | ✓ |
| text | ✓ | ✓ | | ✓ |
| grammar | ✓ | ✓ | | ✓ |
| history | ✓ | ✓ | ✓ | ✓ |
| graph | ✓ | ✓ | | ✓ |
| verdict | ✓✓ | ✓ | | partly |
| expectation | ✓ | ✓ | | ✓ |
| ambient | ✓ | ✗ | | partly |
| identity | ✓ | ✓ | | ✓ |
| execution | ✗ | ✗ | ✓ | |
| remote | ✗ | ✗ | | |

Seven built. `verdict` reads test caches and review ledgers but not compiler or
coverage output; `ambient` reads disks and GPU but not services or scheduled
tasks. The two unbuilt kinds are the two that are not parasitic, and they stay
behind an explicit door rather than being quietly added.

Nine of eleven are parasitic, which is the whole system's precondition. The two
that are not are also the two that answer questions nothing else can, so they
are worth having behind an explicit door rather than pretending they are free.

## Modality — the other axis

The table above classifies endings by what they *need*. Rules care about what
they *sense*, which cuts across it. Both axes are needed: the first decides
cost and failure mode, the second decides which channel a finding belongs to
and whether it is a reflex.

| modality | senses | channel | reflex |
|---|---|---|---|
| **nociception** | damage, and things that are unambiguously wrong | R | yes |
| **mechanoreception** | pressure and load — debt, size, complexity | R | no |
| **proprioception** | shape and position — what this is, how it is put together | — | no |
| **interoception** | internal state — resources, capacity, what is running | R | when critical |
| **chemoreception** | composition — identity, provenance, what something is made of | R/G | no |
| **thermoception** | activity and its direction — warming, cooling | B | no |
| **immune memory** | what has already been judged, and by whom | G/R | no |

The split that matters is nociception against everything else. A conflict marker
and a long function are both "pressure" to a magnitude-only field, and they are
not remotely the same instruction. Nociceptors are sparse, high-threshold, and
almost always silent — the sixteen rules that fire on none of eighteen projects
are exactly that, and their silence is the signal working.

---

**Three failure modes recur across kinds and are the real design constraints:**

1. **Absence reads as safety.** A probe that cannot distinguish "looked and found
   nothing" from "did not look" manufactures false negatives, and this field
   treats silence as licence to skip. Every kind must be able to say *unknown*.
2. **Staleness reads as currency.** Verdict, execution and remote all produce
   answers that keep looking fresh long after they stopped being true.
3. **Confidence without grammar.** Text probes are the cheapest and the most
   wrong; anything they claim needs a second filter before it is believed.
