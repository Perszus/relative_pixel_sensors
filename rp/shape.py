"""Structure: what depends on what, and where things start.

Every other sensor measures *condition* — how damaged a region is, how busy,
how vouched-for. None of them says whether a region matters. A region twenty
other regions import from is a different proposition under the same pressure
than a leaf nobody touches, and pressure alone cannot tell them apart.

This is not a channel. Load-bearing is not good or bad, it is a property of
the shape, and folding it into R would mean asserting that important code is
damaged code. It is kept alongside so the two can be read together.

Costs one `git grep` per repo, index-backed, no filesystem walk.
"""

from __future__ import annotations

import os
import re

from .sensors import SOURCE_EXT, SOURCE_SUFFIX, git, is_ours

# One grep, parsed per language afterwards. Anchored at line start so a mention
# of "import" inside a string or comment mostly stays out.
IMPORT_GREP = r"^[[:space:]]*(import|from|use|mod|export)[[:space:]]"

_PY = re.compile(r"^\s*(?:from|import)\s+([\w.]+)")
_RUST = re.compile(r"^\s*(?:pub\s+)?use\s+(?:crate|super|self)::([\w:]+)")
_RUST_MOD = re.compile(r"^\s*(?:pub\s+)?mod\s+(\w+)\s*;")
_QUOTED = re.compile(r"""["']([^"']+)["']""")
_JVM = re.compile(r"^\s*import\s+([\w.]+)")

# Entry points, by the convention of each ecosystem.
ENTRY_NAMES = (
    "src/main.rs", "src/lib.rs", "main.py", "__main__.py", "app.py",
    "lib/main.dart", "main.go", "index.js", "index.ts", "src/index.ts",
    "manage.py", "cli.py",
)


def _module_candidates(line: str, path: str) -> list[str]:
    """Path fragments an import line might be referring to, ecosystem-agnostic.

    Deliberately a heuristic rather than a resolver per language: the aim is a
    usable shape, and a resolver that is right 95% of the time across five
    ecosystems beats five that are exact and unmaintained.
    """
    ext = os.path.splitext(path)[1].lower()
    out: list[str] = []
    if ext == ".py":
        m = _PY.match(line)
        if m:
            out.append(m.group(1).replace(".", "/"))
    elif ext == ".rs":
        m = _RUST.match(line)
        if m:
            out.append(m.group(1).replace("::", "/"))
        m = _RUST_MOD.match(line)
        if m:
            out.append(m.group(1))
    elif ext in (".dart", ".ts", ".tsx", ".js", ".jsx"):
        m = _QUOTED.search(line)
        if m:
            ref = m.group(1)
            ref = re.sub(r"^package:[^/]+/", "", ref)
            out.append(re.sub(r"\.(dart|ts|tsx|js|jsx)$", "", ref.lstrip("./")))
    elif ext in (".kt", ".java"):
        m = _JVM.match(line)
        if m:
            out.append(m.group(1).replace(".", "/"))
    return [o for o in out if o and not o.startswith(("dart:", "http"))]


def _resolve(candidate: str, index: dict[str, str]) -> str | None:
    """Map a module reference onto a tracked file, longest suffix wins."""
    parts = candidate.strip("/").split("/")
    while parts:
        hit = index.get("/".join(parts).lower())
        if hit:
            return hit
        parts = parts[1:]  # drop the leading package/crate segment and retry
    return None


def analyse(repo: str, label: str, router, files: list[str]) -> dict:
    """Region dependency graph and entry points for one repo.

    Returns {"fan_in": {region: n}, "depends": {region: n}, "entries": [...]}.
    """
    ours = [f for f in files if f.endswith(SOURCE_SUFFIX) and is_ours(f)]
    if not ours:
        return {"fan_in": {}, "depends": {}, "entries": []}

    # Suffix index: every trailing path fragment of every source file, so an
    # import naming only the tail still resolves.
    index: dict[str, str] = {}
    for f in ours:
        stem = re.sub(r"\.[^./]+$", "", f)
        parts = stem.split("/")
        for i in range(len(parts)):
            index.setdefault("/".join(parts[i:]).lower(), f)

    edges: set[tuple[str, str]] = set()
    out = git(repo, "grep", "-I", "-n", "-E", IMPORT_GREP, "--", *SOURCE_EXT)
    for raw in out.splitlines():
        path, _, rest = raw.partition(":")
        _, _, line = rest.partition(":")
        if not path or not is_ours(path) or not path.endswith(SOURCE_SUFFIX):
            continue
        src_region, _ = router.route_and_key(f"{repo}/{path}")
        if src_region is None:
            continue
        for cand in _module_candidates(line, path):
            target = _resolve(cand, index)
            if not target or target == path:
                continue
            dst_region, _ = router.route_and_key(f"{repo}/{target}")
            if dst_region and dst_region != src_region:
                edges.add((src_region, dst_region))

    fan_in: dict[str, int] = {}
    depends: dict[str, int] = {}
    for src, dst in edges:
        fan_in[dst] = fan_in.get(dst, 0) + 1
        depends[src] = depends.get(src, 0) + 1

    tracked = set(files)
    entries = [e for e in ENTRY_NAMES if e in tracked]
    # Anything that looks like a program start, wherever it lives.
    for f in ours:
        base = os.path.basename(f)
        if base in ("main.rs", "main.dart", "main.go", "__main__.py") and f not in entries:
            entries.append(f)
    return {"fan_in": fan_in, "depends": depends, "entries": sorted(entries)[:4]}
