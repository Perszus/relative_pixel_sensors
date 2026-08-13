"""Recognizers and rules.

A **recognizer** says what a thing *is* by its shape, not by where it sits: a
directory holding `Cargo.toml` is a Rust crate wherever it lives. A **rule**
latches onto a recognized kind and extracts one number.

The point of the split is that knowing what something is tells you which
questions are worth asking of it. "Has a lockfile" is meaningless for a Kotlin
project and load-bearing for a Rust one, and a rule scoped to `rust-crate` is
silent on the other seventeen projects rather than reporting a false absence.

That scoping is also what makes volume safe. Thousands of narrowly-latched
rules are not thousands of firing signals -- most are silent almost always,
which is the behaviour a rare-disaster sensor should have. A rule that cannot
stay silent is decoration no matter how many of them there are.

Rules are data. Adding one is a line, not a function.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .probes import PROBES, listing, matches

ANY = "*"


# --------------------------------------------------------------------- shapes


@dataclass(frozen=True)
class Recognizer:
    """A structural signature. `any_of` are globs relative to a candidate root;
    matching one is enough to claim the kind."""
    kind: str
    any_of: tuple[str, ...]
    describes: str = ""


RECOGNIZERS: tuple[Recognizer, ...] = (
    Recognizer("repo", (".git/HEAD", ".git"), "a version-controlled tree"),
    Recognizer("rust-crate", ("Cargo.toml",), "a Rust crate or workspace"),
    # `requirements.txt` is deliberately not a signature. Plenty of projects in
    # other languages carry one for a helper script, and matching on it made an
    # Android app a "Python package" — which then had it compared against
    # Python peers for its missing README. A loose recognizer is how an
    # unearned norm gets in through the back door.
    Recognizer("python-pkg", ("pyproject.toml", "setup.py", "setup.cfg"),
               "a Python package"),
    Recognizer("dart-pkg", ("pubspec.yaml",), "a Dart or Flutter package"),
    Recognizer("node-pkg", ("package.json",), "a Node package"),
    Recognizer("android-app", ("**/AndroidManifest.xml", "settings.gradle",
                               "settings.gradle.kts"), "an Android app"),
    Recognizer("go-mod", ("go.mod",), "a Go module"),
    Recognizer("dotnet", ("**/*.csproj", "**/*.sln"), "a .NET project"),
    Recognizer("cmake", ("CMakeLists.txt",), "a CMake build"),
    Recognizer("docker", ("Dockerfile", "docker-compose.yml", "compose.yaml"),
               "a containerised service"),
    Recognizer("ci", (".github/workflows", ".gitlab-ci.yml"),
               "something with automated checks"),
    Recognizer("web", ("index.html", "**/index.html"), "a static or web front end"),
    Recognizer("reviewed", ("ester_analysis.md",), "a tree a reviewer has read"),
    Recognizer("has-tests", ("tests", "test", "**/*_test.dart", "**/test_*.py"),
               "a tree with a test suite"),
    Recognizer("packaging", ("snap/snapcraft.yaml", "**/*.flatpak",
                             "**/*.desktop", "installer"),
               "a distribution or packaging target"),
    Recognizer("venv", (".venv/pyvenv.cfg", "venv/pyvenv.cfg"),
               "a checked-in Python environment"),
)


def recognize(root: str) -> set[str]:
    """Every kind this root satisfies. A thing is usually several at once — a
    repo AND a rust-crate AND has-tests — and rules latch onto each."""
    kinds: set[str] = set()
    for r in RECOGNIZERS:
        for pattern in r.any_of:
            if matches(root, pattern) or os.path.exists(os.path.join(root, pattern)):
                kinds.add(r.kind)
                break
    return kinds


# ---------------------------------------------------------------------- rules


@dataclass(frozen=True)
class Rule:
    """One declarative sensor.

    `on`    which recognized kind this latches onto, or ANY
    `probe` a name from probes.PROBES
    `args`  arguments after the root
    `chan`  R pressure / G health / B activity, or "" for metadata only
    `w`     multiplier applied to the probe's value
    `cap`   ceiling on the contribution, so one runaway count cannot drown
            every other rule in the channel
    `says`  how the finding reads
    """
    id: str
    on: str
    probe: str
    args: tuple
    chan: str
    w: float
    says: str
    cap: float = 24.0
    kind: str = "standing"

    def evaluate(self, root: str) -> float:
        fn = PROBES.get(self.probe)
        if fn is None:
            return 0.0
        try:
            raw = float(fn(root, *self.args))
        except Exception:
            return 0.0
        if raw <= 0.0 or raw != raw or raw == float("inf"):
            return 0.0
        return min(raw * self.w, self.cap)


def R(id, on, probe, args, chan, w, says, cap=24.0):
    return Rule(id, on, probe, tuple(args), chan, w, says, cap)


# The table. Each line is a sensor.
#
# Weights are deliberately small and capped: with many rules the risk is not
# that one is wrong but that one is loud, and a capped rule can be wrong
# without being able to dominate the channel it writes to.
RULES: tuple[Rule, ...] = (
    # --- hygiene that applies to anything on disk ---------------------------
    R("secret-literal", ANY, "content",
      ("*", r"(api[_-]?key|secret|passwd|password|private[_-]?key)[\"' ]*[:=][\"' ]*[A-Za-z0-9/+=_-]{24,}"),
      "R", 6.0, "credential-shaped literal in tracked source", 24.0),
    R("conflict-markers", ANY, "content", ("*", r"^<{7} |^>{7} "),
      "R", 6.0, "committed merge-conflict markers"),
    R("debt-markers", ANY, "content", ("*", r"TODO|FIXME|HACK|XXX"),
      "R", 0.25, "debt markers left in source", 12.0),
    R("oversized-blobs", ANY, "bytes_over", ("**/*", 2_000_000),
      "R", 1.0, "tracked files over 2 MB", 10.0),
    R("env-file-committed", ANY, "exists", ("**/.env",),
      "R", 6.0, "a .env file is committed"),
    R("keystore-committed", ANY, "count", ("**/*.jks",),
      "R", 8.0, "a signing keystore is committed"),
    R("readme", ANY, "exists", ("README.md",), "G", 1.0, "has a README"),
    R("licence", ANY, "exists", ("LICENSE*",), "G", 1.0, "has a licence"),
    R("docs-dir", ANY, "count", ("docs/*",), "G", 0.2, "has a docs directory", 3.0),
    R("gitignore", "repo", "exists", (".gitignore",), "G", 1.0, "has a .gitignore"),
    R("editorconfig", ANY, "exists", (".editorconfig",), "G", 0.5,
      "has an editorconfig"),

    # --- automated checking --------------------------------------------------
    R("ci-configured", "ci", "count", (".github/workflows/*",),
      "G", 2.0, "automated checks configured", 6.0),
    R("no-ci", "repo", "absent", (".github/workflows",),
      "R", 1.0, "nothing checks this automatically"),
    R("precommit", ANY, "exists", (".pre-commit-config.yaml",),
      "G", 1.5, "pre-commit hooks configured"),

    # --- rust ----------------------------------------------------------------
    R("rust-lockfile", "rust-crate", "exists", ("Cargo.lock",),
      "G", 1.0, "dependencies are pinned"),
    R("rust-fmt", "rust-crate", "exists", ("rustfmt.toml",), "G", 0.5,
      "formatting is pinned"),
    R("rust-unwrap", "rust-crate", "content", ("*.rs", r"\.unwrap\(\)"),
      "R", 0.05, "unwrap() calls that can panic", 8.0),
    R("rust-unsafe", "rust-crate", "content", ("*.rs", r"\bunsafe\b"),
      "R", 0.3, "unsafe blocks", 8.0),
    R("rust-panic", "rust-crate", "content", ("*.rs", r"panic!\("),
      "R", 0.2, "explicit panics", 6.0),

    # --- python --------------------------------------------------------------
    R("py-pinned", "python-pkg", "exists", ("requirements*.txt",),
      "G", 0.5, "dependencies listed"),
    R("py-lock", "python-pkg", "exists", ("poetry.lock",), "G", 1.0,
      "dependencies are pinned"),
    R("py-bare-except", "python-pkg", "content", ("*.py", r"except\s*:"),
      "R", 0.5, "bare except: swallows every error", 8.0),
    R("py-print-debug", "python-pkg", "content", ("*.py", r"^\s*breakpoint\(\)"),
      "R", 4.0, "a breakpoint() left in source"),
    R("py-typed", "python-pkg", "exists", ("py.typed",), "G", 0.5, "ships types"),
    R("py-ruff", "python-pkg", "exists", ("ruff.toml",), "G", 0.5, "linting configured"),

    # --- dart / flutter ------------------------------------------------------
    R("dart-lock", "dart-pkg", "exists", ("pubspec.lock",), "G", 1.0,
      "dependencies are pinned"),
    R("dart-analysis", "dart-pkg", "exists", ("analysis_options.yaml",),
      "G", 1.0, "static analysis configured"),
    R("dart-print", "dart-pkg", "content", ("*.dart", r"^\s*print\("),
      "R", 0.15, "print() calls left in source", 6.0),
    R("dart-ignore", "dart-pkg", "content", ("*.dart", r"// ignore:"),
      "R", 0.3, "analyzer warnings suppressed inline", 6.0),

    # --- node ----------------------------------------------------------------
    R("node-lock", "node-pkg", "exists", ("package-lock.json",), "G", 1.0,
      "dependencies are pinned"),
    R("node-console", "node-pkg", "content", ("*.ts", r"console\.log\("),
      "R", 0.1, "console.log left in source", 5.0),

    # --- android -------------------------------------------------------------
    R("android-debuggable", "android-app", "content",
      ("**/AndroidManifest.xml", r'android:debuggable="true"'),
      "R", 8.0, "debuggable flag set in a manifest"),
    R("android-cleartext", "android-app", "content",
      ("**/AndroidManifest.xml", r'usesCleartextTraffic="true"'),
      "R", 4.0, "cleartext traffic permitted"),
    R("android-proguard", "android-app", "exists", ("**/proguard-rules.pro",),
      "G", 1.0, "release shrinking configured"),

    # --- containers ----------------------------------------------------------
    R("docker-latest-tag", "docker", "content", ("Dockerfile", r"FROM .*:latest"),
      "R", 3.0, "base image pinned to :latest"),

    # --- grammar: shape a regex cannot see -----------------------------------
    # Counting the word "function" is a text question. Knowing one is 120 lines
    # long, nested six deep, or takes nine arguments needs a parser.
    R("long-functions", ANY, "longest_function", ("*", 120),
      "R", 0.6, "functions over 120 lines", 10.0),
    R("very-long-functions", ANY, "longest_function", ("*", 300),
      "R", 2.0, "functions over 300 lines", 10.0),
    R("deep-nesting", ANY, "deep_nesting", ("*", 6),
      "R", 0.8, "files nested more than six deep", 8.0),
    R("wide-signatures", "python-pkg", "wide_signatures", ("*.py", 8),
      "R", 0.5, "functions taking more than eight arguments", 6.0),
    R("mutable-default", "python-pkg", "py_smell", ("mutable_default",),
      "R", 3.0, "mutable default argument — shared between calls", 12.0),
    R("bare-except-ast", "python-pkg", "py_smell", ("bare_except",),
      "R", 1.0, "bare except: swallows everything including exit", 8.0),
    R("broad-except", "python-pkg", "py_smell", ("broad_except",),
      "R", 0.3, "except Exception: hides unrelated failures", 6.0),
    R("star-import", "python-pkg", "py_smell", ("star_import",),
      "R", 1.0, "star import — the namespace is now unknowable", 6.0),
    R("shadowed-builtin", "python-pkg", "py_smell", ("shadowed_builtin",),
      "R", 1.0, "a builtin name is shadowed", 6.0),
    R("global-statement", "python-pkg", "py_smell", ("global_statement",),
      "R", 0.8, "global statements", 6.0),
    R("dead-privates", "python-pkg", "unused_privates", ("*.py",),
      "R", 0.4, "private functions defined and never called", 6.0),
    R("untyped", "python-pkg", "untyped_share", ("*.py",),
      "G", 0.0, "share of parameters without annotations"),

    # --- history: the only kind that can say "getting worse" -----------------
    # Every window is stated in the rule, because a count without its horizon
    # is a number nobody can compare to anything.
    R("churn-accelerating", "repo", "churn_acceleration", (30,),
      "R", 1.5, "change is accelerating — more churn this month than last", 8.0),
    R("hotspots", "repo", "hotspots", (90, 12),
      "R", 0.8, "files changed more than 12 times in 90 days", 8.0),
    R("repair-heavy", "repo", "fix_ratio", (90,),
      "R", 0.06, "share of commits in 90 days that are repairs", 8.0),
    R("reverts", "repo", "reverts", (90,),
      "R", 1.5, "commits reverted in 90 days — changes that should not have shipped", 8.0),
    R("patched-not-developed", "repo", "fix_only_files", (180,),
      "R", 1.0, "files only ever touched to repair something", 8.0),
    R("sweeping-commits", "repo", "big_commits", (90, 40),
      "R", 0.3, "commits touching more than 40 files — unreviewable in one sitting", 5.0),
    R("dormant", "repo", "stagnant_days", (0,),
      "R", 0.0, "days since the last commit"),
    # Deliberately a whisper. It is measured correctly now, but on a young
    # fleet almost every project scores near the cap, and a signal every
    # subject scores the same on changes no ranking. Kept because it does
    # discriminate on a mature codebase, weighted so it cannot inflate R here.
    R("new-code", "repo", "new_code_share", (90,),
      "R", 0.004, "share of the source tree created in the last 90 days", 1.5),
    R("active", "repo", "commits_in", (30,),
      "B", 0.4, "commits in the last 30 days", 20.0),

    # --- expectation: the subject disagreeing with itself --------------------
    # No outside standard involved. The project declared both of these things,
    # and they do not match.
    R("version-drift", "repo", "version_disagreement", (0,),
      "R", 3.0, "manifest version disagrees with the newest release tag"),
    R("undeclared-deps", "python-pkg", "undeclared_deps", (0,),
      "R", 2.0, "imports never declared as dependencies — works here, not elsewhere",
      12.0),

    # --- identity: what a thing is, not where it sits ------------------------
    R("duplicate-source", ANY, "duplicate_files", (0,),
      "R", 1.0, "byte-identical source files — copy-paste that never got "
                "refactored", 8.0),
    R("stale-binaries", ANY, "stale_binaries", (0,),
      "R", 2.0, "committed build output older than the source it came from", 8.0),

    # --- review and testing --------------------------------------------------
    R("reviewed", "reviewed", "exists", ("ester_analysis.md",), "G", 1.0,
      "has been reviewed"),
    R("unreviewed", "repo", "absent", ("ester_analysis.md",), "R", 1.0,
      "nothing has ever reviewed this"),
    R("has-tests", "has-tests", "count", ("**/test_*.py",), "G", 0.2,
      "has a test suite", 4.0),

    # --- verdict: what another tool already concluded ------------------------
    # Both refuse to answer from expired evidence rather than reporting a stale
    # pass, because a verdict that keeps looking current after it stopped being
    # true is the most dangerous failure in the system.
    R("tests-failing-now", ANY, "failing_test_share", (0,),
      "R", 0.4, "share of collected tests failing at the last run", 12.0),
    R("log-errors", ANY, "log_errors", (7,),
      "R", 0.5, "errors written to this project's own logs in the last week",
      10.0),
)

# Machine-wide rules. Not scoped to a subject on disk, because "the system
# drive is full" is not a property of any one project -- it is a property of
# the environment every project sits in, and attaching it to eighteen subjects
# would report one fact eighteen times.
MACHINE_RULES: tuple[Rule, ...] = (
    R("disk-system-low", ANY, "disk_free_pct", ("C",), "R", 0.0,
      "free space on the system drive"),
    R("disk-work-low", ANY, "disk_free_pct", ("F",), "R", 0.0,
      "free space on the work drive"),
    R("vram-in-use", ANY, "vram_used_pct", (0,), "", 0.0, "VRAM in use"),
)


def machine() -> list[Finding]:
    """The environment itself, measured once rather than per subject.

    Thresholds live here rather than in the probes: a probe reports a number and
    forms no opinion, so "5% free is bad" is a rule's judgement to make.

    Everything here is a LEVEL. Ambient values are true now and meaningless as
    history, so they are set rather than accumulated — a disk that was full an
    hour ago and is fine now reads as fine.
    """
    import string

    from . import ambient
    from .probes import disk_free_pct, gpu

    out: list[Finding] = []

    def say(rule: str, weight: float, words: str) -> None:
        out.append(Finding(rule, "machine", "machine", "R", weight, words))

    # --- storage. Every fixed volume, not a hardcoded pair.
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if not os.path.isdir(root):
            continue
        free = disk_free_pct("", letter)
        if free < 12.0:
            # Steep: 11% free is a note and 1% free is an emergency, and a
            # linear scale reports them as nearly the same thing.
            say(f"disk-{letter.lower()}-low", min((12.0 - free) ** 2 / 5.0, 24.0),
                f"{letter}: is {free:.1f}% free")

    for label, gb in ambient.largest_caches(ambient.KNOWN_CACHES, 20.0):
        say("cache-large", min(gb / 20.0, 6.0),
            f"{label} cache is {gb:.0f} GB")

    # --- memory and compute
    used, total = gpu()
    if total and used / total > 0.85:
        say("vram-pressure", 4.0, f"VRAM {used:.0f} of {total:.0f} MB in use")
    mem = ambient.memory_used_pct()
    if mem > 90.0:
        say("memory-pressure", (mem - 90.0), f"memory {mem:.0f}% in use")
    commit = ambient.commit_used_pct()
    if commit > 90.0:
        # Commit exhaustion is what actually fails an allocation; a machine can
        # have free RAM and no commit left.
        say("commit-pressure", (commit - 90.0) * 1.5,
            f"commit charge {commit:.0f}% — allocations will start failing")

    # --- condition
    if ambient.pending_reboot():
        say("pending-reboot", 3.0, "a restart is pending — changes may not have "
                                   "taken effect")
    up = ambient.uptime_days()
    if up > 21.0:
        say("long-uptime", min(up / 21.0, 3.0), f"up {up:.0f} days without a restart")

    failed = ambient.failed_services()
    if failed:
        say("services-down", min(len(failed) * 1.5, 8.0),
            f"{len(failed)} automatic service(s) not running: "
            f"{', '.join(failed[:3])}")

    tasks = ambient.scheduled_task_failures()
    if tasks:
        say("scheduled-tasks-failing", min(tasks * 1.0, 6.0),
            f"{tasks} scheduled task(s) last ran unsuccessfully")

    models = ambient.resident_models()
    if models:
        total_gb = sum(g for _, g in models)
        if total_gb > 4.0:
            say("models-resident", min(total_gb / 4.0, 4.0),
                f"{len(models)} model(s) resident, {total_gb:.1f} GB: "
                f"{models[0][0]}")
    return out


@dataclass
class Finding:
    rule: str
    subject: str
    kind: str
    channel: str
    value: float
    says: str


def evaluate(subject: str, root: str, kinds: set[str]) -> list[Finding]:
    """Every rule that latches onto this subject and has something to say."""
    out: list[Finding] = []
    for rule in RULES:
        if rule.on != ANY and rule.on not in kinds:
            continue
        if not rule.chan or rule.w == 0.0:
            continue
        value = rule.evaluate(root)
        if value <= 0.0:
            continue
        out.append(Finding(rule.id, subject, rule.on, rule.chan, value, rule.says))
    return out


def rule_count() -> tuple[int, int]:
    """(rules that can emit, recognizers)."""
    return sum(1 for r in RULES if r.chan and r.w), len(RECOGNIZERS)
