"""Recognizers, probes and rules.

The engine's failure mode is different from a hand-written sensor's: a broken
probe does not crash, it makes every rule using it fire or fall silent across
the whole fleet at once. Both happened while building it, so both are pinned.
"""

import os

import pytest

from rp import probes, rules


@pytest.fixture
def tree(tmp_path):
    """A small Rust crate with a CI directory and an untracked report."""
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    (tmp_path / "Cargo.lock").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text(
        "fn main(){ let x = foo().unwrap(); panic!(\"no\"); }\n", encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("on: push\n",
                                                               encoding="utf-8")
    (tmp_path / "ester_analysis.md").write_text("# review\n", encoding="utf-8")
    probes.clear_caches()
    return str(tmp_path)


def test_recognizes_by_structure_not_location(tree):
    """A Cargo.toml makes a rust-crate wherever it sits — that is the whole
    reason this is not tied to a folder layout."""
    kinds = rules.recognize(tree)
    assert "rust-crate" in kinds
    assert "ci" in kinds
    assert "reviewed" in kinds


def test_recognizes_nothing_in_an_empty_tree(tmp_path):
    probes.clear_caches()
    assert rules.recognize(str(tmp_path)) == set()


def test_exists_sees_directories(tree):
    """`.github/workflows` is a directory. Globbing git's index for it returned
    nothing, so `no-ci` fired on all eighteen projects while the recognizers,
    which stat, correctly disagreed."""
    assert probes.exists(tree, ".github/workflows") == 1.0
    assert probes.absent(tree, ".github/workflows") == 0.0


def test_exists_sees_untracked_files(tree):
    """ester_analysis.md is generated and gitignored; the index cannot see it."""
    assert probes.exists(tree, "ester_analysis.md") == 1.0


def test_count_does_not_fall_back_to_the_root(tree):
    """`**/*.jks` has an empty directory prefix. Counting the project root for
    it made a keystore-detector fire on every project in the fleet."""
    assert probes.count(tree, "**/*.jks") == 0.0


def test_count_falls_back_for_a_real_directory(tree):
    assert probes.count(tree, ".github/workflows/*") == 1.0


def test_rules_scoped_to_a_kind_stay_silent_elsewhere(tmp_path):
    """Narrow scoping is what makes volume safe: a Rust rule must say nothing
    about seventeen non-Rust projects rather than reporting a false absence."""
    (tmp_path / "main.py").write_text("print(1)\n", encoding="utf-8")
    probes.clear_caches()
    kinds = rules.recognize(str(tmp_path))
    fired = {f.rule for f in rules.evaluate("p", str(tmp_path), kinds)}
    assert not any(r.startswith("rust-") for r in fired)


def test_rules_fire_on_the_kind_they_latch_onto(tree):
    fired = {f.rule for f in rules.evaluate("x", tree, rules.recognize(tree))}
    assert "rust-lockfile" in fired
    assert "rust-unwrap" in fired
    assert "ci-configured" in fired
    assert "no-ci" not in fired          # it has CI
    assert "unreviewed" not in fired     # it has a review


def test_every_rule_names_a_real_probe():
    for rule in (*rules.RULES, *rules.MACHINE_RULES):
        assert rule.probe in probes.PROBES, rule.id


def test_rule_ids_are_unique():
    ids = [r.id for r in (*rules.RULES, *rules.MACHINE_RULES)]
    assert len(ids) == len(set(ids))


def test_every_emitting_rule_has_a_channel_and_words():
    """A rule that fires without saying why is an anonymous number, and at this
    volume nobody can trace it back."""
    for rule in rules.RULES:
        if rule.w:
            assert rule.chan in ("R", "G", "B"), rule.id
            assert rule.says, rule.id


def test_contributions_are_capped():
    """With many rules the risk is not that one is wrong but that one is loud.
    A capped rule can be wrong without drowning its channel."""
    for rule in rules.RULES:
        assert rule.cap <= 24.0, rule.id


def test_evaluate_survives_a_broken_probe(tree, monkeypatch):
    """One bad rule must not take the pass down with it."""
    def boom(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setitem(probes.PROBES, "exists", boom)
    rules.evaluate("x", tree, rules.recognize(tree))  # must not raise


def test_machine_findings_are_not_attached_to_projects():
    """"The system drive is full" is a property of the environment, not of any
    project; attaching it to each would report one fact eighteen times."""
    for f in rules.machine():
        assert f.subject == "machine"
