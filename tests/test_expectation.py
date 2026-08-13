"""EXPECTATION probes.

The failure mode of this kind is unearned norms — findings that are technically
true and entirely unwanted, which teach a reader to skip the section. Every test
here is about where the expectation came from, because that is the only thing
that separates this kind from opinion.
"""

import pytest

from rp import expectation, probes


def _subject(tmp_path, name, files, kinds):
    root = tmp_path / name
    root.mkdir()
    for f in files:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    probes.clear_caches()
    return name, (str(root), set(kinds))


def test_peer_gap_needs_enough_peers(tmp_path):
    """Two projects do not establish a norm. A small or heterogeneous fleet
    gets silence, not a fallback to opinion."""
    subjects = dict([
        _subject(tmp_path, "a", ["README.md"], {"rust-crate"}),
        _subject(tmp_path, "b", [], {"rust-crate"}),
    ])
    assert expectation.peer_gaps(subjects) == {}


def test_peer_gap_fires_when_most_peers_agree(tmp_path):
    subjects = dict([
        _subject(tmp_path, "a", ["README.md"], {"rust-crate"}),
        _subject(tmp_path, "b", ["README.md"], {"rust-crate"}),
        _subject(tmp_path, "c", ["README.md"], {"rust-crate"}),
        _subject(tmp_path, "d", ["README.md"], {"rust-crate"}),
        _subject(tmp_path, "odd", ["main.rs"], {"rust-crate"}),
    ])
    gaps = expectation.peer_gaps(subjects)
    assert "odd" in gaps
    assert any("README" in g for g in gaps["odd"])
    assert set(gaps) == {"odd"}


def test_peer_gap_stays_silent_when_peers_disagree(tmp_path):
    """Half the fleet having a thing is not a norm. Below the agreement
    threshold there is nothing to be missing from."""
    subjects = dict([
        _subject(tmp_path, "a", ["CHANGELOG.md"], {"rust-crate"}),
        _subject(tmp_path, "b", ["CHANGELOG.md"], {"rust-crate"}),
        _subject(tmp_path, "c", [], {"rust-crate"}),
        _subject(tmp_path, "d", [], {"rust-crate"}),
        _subject(tmp_path, "e", [], {"rust-crate"}),
    ])
    gaps = expectation.peer_gaps(subjects)
    assert not any("CHANGELOG" in g for v in gaps.values() for g in v)


def test_peers_are_scoped_to_a_kind(tmp_path):
    """A Dart package's norms say nothing about a Rust crate. Comparing across
    kinds is how a norm nobody agreed to gets applied."""
    subjects = dict([
        _subject(tmp_path, "d1", ["README.md"], {"dart-pkg"}),
        _subject(tmp_path, "d2", ["README.md"], {"dart-pkg"}),
        _subject(tmp_path, "d3", ["README.md"], {"dart-pkg"}),
        _subject(tmp_path, "d4", ["README.md"], {"dart-pkg"}),
        _subject(tmp_path, "r1", ["main.rs"], {"rust-crate"}),
    ])
    assert "r1" not in expectation.peer_gaps(subjects)


def test_peer_gap_names_the_evidence(tmp_path):
    """A finding has to say who set the norm, or it is indistinguishable from
    an opinion the tool holds."""
    subjects = dict([
        _subject(tmp_path, f"p{i}", ["README.md"], {"rust-crate"})
        for i in range(4)
    ] + [_subject(tmp_path, "odd", ["x.rs"], {"rust-crate"})])
    gap = expectation.peer_gaps(subjects)["odd"][0]
    assert "peers have one" in gap
    assert "rust-crate" in gap


def test_undeclared_imports_silent_without_a_manifest(tmp_path):
    """A project that declares no dependencies has not claimed a set to be
    incomplete."""
    (tmp_path / "a.py").write_text("import requests\n", encoding="utf-8")
    probes.clear_caches()
    expectation.undeclared_imports.cache_clear()
    assert expectation.undeclared_imports(str(tmp_path)) == ()


def test_undeclared_imports_ignores_stdlib_and_local(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests\n", encoding="utf-8")
    (tmp_path / "helper.py").write_text("", encoding="utf-8")
    (tmp_path / "a.py").write_text(
        "import os\nimport json\nimport helper\nimport requests\n", encoding="utf-8")
    probes.clear_caches()
    expectation.undeclared_imports.cache_clear()
    assert expectation.undeclared_imports(str(tmp_path)) == ()


def test_undeclared_imports_finds_a_real_gap(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("import requests\nimport numpy\n", encoding="utf-8")
    probes.clear_caches()
    expectation.undeclared_imports.cache_clear()
    assert expectation.undeclared_imports(str(tmp_path)) == ("numpy",)


def test_import_name_differing_from_distribution(tmp_path):
    """`import yaml` is satisfied by `pyyaml`. Reporting it as undeclared is
    the sort of technically-shaped falsehood that discredits a section."""
    (tmp_path / "pyproject.toml").write_text("deps = ['pyyaml']", encoding="utf-8")
    (tmp_path / "a.py").write_text("import yaml\n", encoding="utf-8")
    probes.clear_caches()
    expectation.undeclared_imports.cache_clear()
    assert expectation.undeclared_imports(str(tmp_path)) == ()


def test_recognizer_does_not_call_everything_a_python_package():
    """`requirements.txt` is carried by plenty of projects in other languages
    for a helper script, and matching on it made an Android app a Python
    package — which then had it judged against Python peers."""
    from rp import rules
    rec = next(r for r in rules.RECOGNIZERS if r.kind == "python-pkg")
    assert "requirements.txt" not in rec.any_of
