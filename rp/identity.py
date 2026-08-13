"""IDENTITY probes — what an artifact actually is.

Independent of where it sits or what it is called. A path is not an identity:
two files with the same name can be different things, and the same bytes can
appear under three names in three repositories.

The governing risk for this kind is **trust transfer**. A valid signature says
who signed something, not whether it is safe, and reporting one as the other is
worse than silence. Nothing here converts a provenance fact into a safety
claim.

Hashing is IO-bound, so every digest is cached on (path, size, mtime) — the
cheap triple that changes whenever content does.
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache

from .sensors import SOURCE_SUFFIX, is_ours

# Beyond this a file is not source and hashing it is not worth the read.
MAX_HASH_BYTES = 4_000_000


@lru_cache(maxsize=20_000)
def _digest(path: str, size: int, mtime: int) -> str:
    """Content hash. `size` and `mtime` are cache keys, not inputs — they make
    the digest recompute exactly when the file could have changed."""
    h = hashlib.blake2b(digest_size=16)
    try:
        with open(path, "rb") as fh:
            while chunk := fh.read(65536):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def digest(path: str) -> str:
    try:
        st = os.stat(path)
    except OSError:
        return ""
    if st.st_size > MAX_HASH_BYTES:
        return ""
    return _digest(path, st.st_size, int(st.st_mtime))


@lru_cache(maxsize=20_000)
def _text_digest(path: str, size: int, mtime: int) -> str:
    """Content hash with line endings normalised.

    Two checkouts of the same file, one CRLF and one LF, are byte-different and
    textually identical. Hashing them raw would report every shared file in a
    pair of forks as diverged — technically true and entirely useless, which is
    the failure this whole kind is most prone to.
    """
    h = hashlib.blake2b(digest_size=16)
    try:
        with open(path, "rb") as fh:
            while chunk := fh.read(65536):
                h.update(chunk.replace(b"\r\n", b"\n"))
    except OSError:
        return ""
    return h.hexdigest()


def text_digest(path: str) -> str:
    try:
        st = os.stat(path)
    except OSError:
        return ""
    if st.st_size > MAX_HASH_BYTES:
        return ""
    return _text_digest(path, st.st_size, int(st.st_mtime))


def source_digests(root: str, files: list[str]) -> dict[str, str]:
    """rel path -> content hash, for the source files that are ours."""
    out: dict[str, str] = {}
    for rel in files:
        if not rel.endswith(SOURCE_SUFFIX) or not is_ours(rel):
            continue
        d = text_digest(os.path.join(root, rel))
        if d:
            out[rel] = d
    return out


def duplicates_within(digests: dict[str, str]) -> int:
    """Files whose bytes appear more than once in the same project.

    Byte-identical duplicates are copy-paste that never got refactored, and
    unlike most duplication findings this one has no false positives: the files
    are the same file.
    """
    seen: dict[str, int] = {}
    for d in digests.values():
        seen[d] = seen.get(d, 0) + 1
    return sum(n - 1 for n in seen.values() if n > 1)


def divergence(a: dict[str, str], b: dict[str, str]) -> tuple[int, int, list[str]]:
    """How far two near-identical projects have drifted.

    Returns (shared filenames, diverged, examples).

    This is what filename comparison cannot do. Knowing two repositories are
    packaging forks of the same program is mildly interesting; knowing that
    twelve of their two hundred shared files no longer contain the same bytes
    is the actionable half — those twelve are where a fix applied to one and
    not the other will hide.

    Compares by basename rather than full path, because forks routinely move
    files while keeping them the same.
    """
    def by_name(d: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for rel, h in d.items():
            out.setdefault(os.path.basename(rel), h)
        return out

    na, nb = by_name(a), by_name(b)
    shared = set(na) & set(nb)
    diverged = sorted(name for name in shared if na[name] != nb[name])
    return len(shared), len(diverged), diverged[:5]


def stale_artifacts(root: str, files: list[str]) -> int:
    """Committed build output older than the source beside it.

    A binary in the tree that predates the code it was built from is a lie
    waiting to be believed by whoever picks it up.
    """
    newest_source = 0.0
    artifacts: list[tuple[str, float]] = []
    for rel in files:
        full = os.path.join(root, rel)
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        if rel.endswith(SOURCE_SUFFIX) and is_ours(rel):
            newest_source = max(newest_source, mtime)
        elif rel.lower().endswith((".exe", ".dll", ".so", ".dylib", ".jar",
                                   ".aar", ".apk", ".aab", ".wasm")):
            artifacts.append((rel, mtime))
    if not newest_source:
        return 0
    # A day of slack: a build and the commit that followed it are the same
    # event as far as this question goes.
    return sum(1 for _, m in artifacts if m < newest_source - 86400.0)
