"""EXPECTATION probes — comparison against a norm.

A different epistemic act from the rest. "No tests" is not an observation, it is
an inference from an expectation, and that expectation has to come from
somewhere defensible.

The failure mode of this kind is **unearned norms**: findings that are
technically true and entirely unwanted, which teach a reader to skip the whole
section. Encoding one house's conventions as anatomy is how it happens.

So every expectation here is derived, never authored:

  * from **peers** — if fifteen of eighteen projects of a kind have a thing and
    this one does not, the fleet set that norm, not an opinion. This is the
    strongest form: it costs no judgement, adapts to any system it is pointed
    at, and cannot express a preference nobody holds.
  * from the subject's own **declarations** — a manifest that disagrees with its
    lockfile or its tag is inconsistent with itself, and needs no outside
    standard to say so.

Nothing here compares against a list of what good projects ought to look like.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

from .probes import exists, git, listing
from .sensors import SOURCE_SUFFIX, is_ours

# Observable features a peer comparison can be made over. Each is something a
# project either has or does not, cheaply checkable, and meaningful by absence.
# The list is what CAN be compared; what SHOULD be expected is decided by the
# fleet's own distribution, not here.
FEATURES: tuple[tuple[str, str], ...] = (
    ("a lockfile", "**/*.lock"),
    ("a CI workflow", ".github/workflows"),
    ("a README", "README.md"),
    ("a licence", "LICENSE"),
    ("a .gitignore", ".gitignore"),
    ("an editorconfig", ".editorconfig"),
    ("a docs directory", "docs"),
    ("a tests directory", "tests"),
    ("a changelog", "CHANGELOG.md"),
    ("static analysis config", "analysis_options.yaml"),
    ("a linter config", "ruff.toml"),
    ("pre-commit hooks", ".pre-commit-config.yaml"),
    ("an issue template", ".github/ISSUE_TEMPLATE"),
    ("a contributing guide", "CONTRIBUTING.md"),
    ("a dependabot config", ".github/dependabot.yml"),
    ("an editor workspace config", ".vscode"),
)

# A norm needs enough peers to be a norm, and enough of them to agree.
MIN_PEERS = 4
AGREEMENT = 0.7


def features(root: str) -> set[str]:
    """Which comparable features this subject has."""
    return {label for label, pattern in FEATURES if exists(root, pattern)}


def peer_gaps(subjects: dict[str, tuple[str, set[str]]]) -> dict[str, list[str]]:
    """What each subject lacks that most of its peers have.

    `subjects` maps label -> (root, recognized kinds). Peers are subjects
    sharing a kind, so a Rust crate is compared against Rust crates rather than
    against everything — the norm for a Dart package says nothing about it.

    Returns label -> list of missing features. Silent when there are too few
    peers to establish anything, which is the honest answer for a small or
    heterogeneous fleet rather than an excuse to fall back on opinion.
    """
    have = {label: features(root) for label, (root, _) in subjects.items()}
    kinds: dict[str, list[str]] = {}
    for label, (_, subject_kinds) in subjects.items():
        for kind in subject_kinds:
            kinds.setdefault(kind, []).append(label)

    gaps: dict[str, set[str]] = {}
    for kind, members in kinds.items():
        if len(members) < MIN_PEERS:
            continue
        for feature, _ in FEATURES:
            holders = [m for m in members if feature in have[m]]
            if len(holders) / len(members) < AGREEMENT:
                continue
            for m in members:
                if feature not in have[m]:
                    gaps.setdefault(m, set()).add(
                        f"{feature} — {len(holders)} of {len(members)} "
                        f"{kind} peers have one")
    return {label: sorted(v) for label, v in gaps.items()}


# --------------------------------------------------- self-consistency probes


_MANIFESTS = {
    "Cargo.toml": (r'^\s*version\s*=\s*"([^"]+)"', "Cargo.lock"),
    "pubspec.yaml": (r'^\s*version:\s*([^\s#]+)', "pubspec.lock"),
    "package.json": (r'"version"\s*:\s*"([^"]+)"', "package-lock.json"),
    "pyproject.toml": (r'^\s*version\s*=\s*"([^"]+)"', None),
}


@lru_cache(maxsize=64)
def version_drift(root: str) -> tuple[str, str] | None:
    """A manifest version that disagrees with the newest release tag.

    Needs no external standard: the project declared both, and they do not
    match. Silent when there are no tags, because a project that does not tag
    releases has not made a claim to contradict.
    """
    tags = git(root, "tag", "--sort=-v:refname").splitlines()
    tag = next((t.strip() for t in tags if re.match(r"^v?\d+\.\d+", t.strip())), "")
    if not tag:
        return None
    for manifest, (pattern, _) in _MANIFESTS.items():
        if not os.path.isfile(os.path.join(root, manifest)):
            continue
        try:
            with open(os.path.join(root, manifest), encoding="utf-8",
                      errors="replace") as fh:
                m = re.search(pattern, fh.read(), re.M)
        except OSError:
            continue
        if not m:
            continue
        declared = m.group(1).strip().lstrip("v")
        released = tag.lstrip("v")
        # Compare only the numeric core: a tag may carry a suffix the manifest
        # does not, and that is not a disagreement.
        if declared.split("+")[0].split("-")[0] != released.split("+")[0].split("-")[0]:
            return declared, tag
    return None


_IMPORT_TO_DIST = {
    # Import names that differ from the distribution that provides them. Only
    # the ones common enough to cause false positives are worth listing; the
    # probe stays silent on anything it cannot resolve rather than guessing.
    "yaml": "pyyaml", "PIL": "pillow", "cv2": "opencv-python",
    "sklearn": "scikit-learn", "bs4": "beautifulsoup4", "dotenv": "python-dotenv",
    "serial": "pyserial", "OpenGL": "pyopengl", "attr": "attrs",
}


@lru_cache(maxsize=64)
def undeclared_imports(root: str) -> tuple[str, ...]:
    """Third-party Python imports absent from the project's declared deps.

    A dependency that works on this machine and is written down nowhere is a
    dependency that will not work on the next one. Compared against what the
    project itself declares, so no outside notion of correctness is involved.

    Silent unless the project actually declares dependencies somewhere — a
    project with no manifest has not claimed a set to be incomplete.
    """
    declared = ""
    for name in ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile"):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    declared += fh.read().lower()
            except OSError:
                pass
    if not declared.strip():
        return ()

    local = {os.path.splitext(os.path.basename(f))[0]
             for f in listing(root) if f.endswith(".py")}
    local |= {f.split("/", 1)[0] for f in listing(root) if "/" in f}

    stdlib = _stdlib()
    seen: set[str] = set()
    out: set[str] = set()
    for rel in listing(root):
        if not rel.endswith(".py") or not is_ours(rel):
            continue
        try:
            with open(os.path.join(root, rel), encoding="utf-8",
                      errors="replace") as fh:
                for line in fh:
                    m = re.match(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)", line)
                    if not m:
                        continue
                    mod = m.group(1)
                    if mod in seen:
                        continue
                    seen.add(mod)
                    if mod in stdlib or mod in local or mod.startswith("_"):
                        continue
                    dist = _IMPORT_TO_DIST.get(mod, mod).lower()
                    if dist not in declared and mod.lower() not in declared:
                        out.add(mod)
        except OSError:
            continue
    return tuple(sorted(out))


@lru_cache(maxsize=1)
def _stdlib() -> frozenset[str]:
    import sys
    names = set(getattr(sys, "stdlib_module_names", ()))
    return frozenset(names or {"os", "sys", "re", "json", "time", "math"})
