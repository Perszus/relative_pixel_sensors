"""Explaining a finding.

A confidently wrong reading is only survivable if checking it is cheap. These
tests hold the properties that make it cheap: every finding names its probe,
re-runs it live, and states what would falsify it — and nothing in the path
crashes on the awkward cases.
"""

import pytest

import explain
from rp import probes, rules


def test_every_rule_can_state_a_falsifier():
    """A finding a reader cannot disprove is an opinion, not a measurement."""
    for rule in (*rules.RULES, *rules.MACHINE_RULES):
        text = explain._falsifier(rule)
        assert text and not text.startswith("if "), rule.id


def test_falsifier_for_an_unknown_rule_says_so():
    assert "unknown rule" in explain._falsifier(None)


def test_content_rules_mention_the_escape_hatch():
    """Text rules are the ones most likely to be wrong, so their falsifier has
    to point at the deliberate way out rather than implying the code must
    change."""
    rule = next(r for r in rules.RULES if r.probe == "content")
    assert "rp:allow" in explain._falsifier(rule)


def test_rerun_reports_unknown_rather_than_a_number(tmp_path, monkeypatch):
    rule = next(r for r in rules.RULES if r.chan and r.w)
    monkeypatch.setitem(probes.PROBES, rule.probe,
                        lambda *a, **k: float("nan"))
    assert "UNKNOWN" in explain._rerun(rule, str(tmp_path))


def test_rerun_survives_a_probe_that_raises(tmp_path, monkeypatch):
    """One broken probe must not take the explanation down with it."""
    rule = next(r for r in rules.RULES if r.chan and r.w)

    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setitem(probes.PROBES, rule.probe, boom)
    assert "raised RuntimeError" in explain._rerun(rule, str(tmp_path))


def test_rerun_reports_a_missing_probe():
    rule = rules.Rule("x", "*", "no_such_probe", (), "R", 1.0, "x")
    assert "no longer exists" in explain._rerun(rule, ".")


def test_lookup_finds_machine_rules_too():
    assert explain._rule("vulnerable-deps") is not None
    assert explain._rule("definitely-not-a-rule") is None
