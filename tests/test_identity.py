"""IDENTITY probes.

A path is not an identity. The risk of the kind is trust transfer — treating a
provenance fact as a safety claim — and its most likely false positive is
mistaking an encoding difference for a real one.
"""

import os
import time

import pytest

from rp import identity


def _write(path, body, newline="\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.replace("\n", newline).encode())
    return str(path)


def test_same_bytes_same_digest(tmp_path):
    a = _write(tmp_path / "a.py", "print(1)\n")
    b = _write(tmp_path / "b.py", "print(1)\n")
    assert identity.digest(a) == identity.digest(b)


def test_different_bytes_different_digest(tmp_path):
    a = _write(tmp_path / "a.py", "print(1)\n")
    b = _write(tmp_path / "b.py", "print(2)\n")
    assert identity.digest(a) != identity.digest(b)


def test_line_endings_do_not_count_as_divergence(tmp_path):
    """Two checkouts of one file, CRLF and LF, are byte-different and textually
    identical. Hashing raw would report every shared file in a pair of forks as
    diverged — technically true and entirely useless."""
    a = _write(tmp_path / "crlf" / "x.py", "a\nb\nc\n", newline="\r\n")
    b = _write(tmp_path / "lf" / "x.py", "a\nb\nc\n", newline="\n")
    assert identity.digest(a) != identity.digest(b)          # raw bytes differ
    assert identity.text_digest(a) == identity.text_digest(b)  # content does not


def test_missing_file_is_empty_not_an_error(tmp_path):
    assert identity.digest(str(tmp_path / "nope.py")) == ""


def test_oversized_file_is_skipped(tmp_path, monkeypatch):
    """Hashing is IO-bound and a huge file is not source."""
    monkeypatch.setattr(identity, "MAX_HASH_BYTES", 10)
    p = _write(tmp_path / "big.py", "x" * 100)
    assert identity.digest(p) == ""


def test_duplicates_within_counts_extra_copies(tmp_path):
    digests = {"a.py": "h1", "b.py": "h1", "c.py": "h1", "d.py": "h2"}
    # Three copies of one file is two duplicates, not three.
    assert identity.duplicates_within(digests) == 2


def test_no_duplicates_is_zero():
    assert identity.duplicates_within({"a.py": "h1", "b.py": "h2"}) == 0


def test_divergence_compares_by_basename(tmp_path):
    """Forks routinely move files while keeping them the same, so a full-path
    comparison would report a rename as a divergence."""
    a = {"lib/x.dart": "same", "lib/y.dart": "aaa"}
    b = {"src/x.dart": "same", "src/y.dart": "bbb"}
    shared, drift, examples = identity.divergence(a, b)
    assert shared == 2
    assert drift == 1
    assert examples == ["y.dart"]


def test_divergence_silent_when_identical():
    a = {"x.py": "h1", "y.py": "h2"}
    assert identity.divergence(a, dict(a))[1] == 0


def test_stale_artifacts_flags_binary_older_than_source(tmp_path):
    src = _write(tmp_path / "main.rs", "fn main(){}")
    exe = _write(tmp_path / "app.exe", "binary")
    old = time.time() - 10 * 86400
    os.utime(exe, (old, old))
    assert identity.stale_artifacts(str(tmp_path), ["main.rs", "app.exe"]) == 1


def test_fresh_artifact_is_not_stale(tmp_path):
    _write(tmp_path / "main.rs", "fn main(){}")
    _write(tmp_path / "app.exe", "binary")
    assert identity.stale_artifacts(str(tmp_path), ["main.rs", "app.exe"]) == 0


def test_no_source_means_no_claim(tmp_path):
    """Without source there is nothing for the artifact to be older than."""
    _write(tmp_path / "app.exe", "binary")
    assert identity.stale_artifacts(str(tmp_path), ["app.exe"]) == 0
