"""The reflex tier.

A reflex does not go to the brain. These findings are reported first, in full,
in no order and with no magnitude, because a committed credential is not a
larger version of a long function — it is a different kind of statement, and
putting it in a ranking invites it to be compared.

The tests are mostly about restraint: a reflex that fires routinely is not a
reflex, and the tier is only worth reading because it is almost always empty.
"""

import pytest

from rp import rules, serve


def test_reflexes_are_few():
    """Five out of sixty-seven. If this grows past a handful, the tier has
    stopped meaning anything and everything is an emergency again."""
    marked = rules.reflexes()
    assert 0 < len(marked) <= 8, marked


def test_reflexes_are_the_unambiguous_ones():
    marked = set(rules.reflexes())
    for expected in ("secret-literal", "conflict-markers", "env-file-committed",
                     "keystore-committed", "android-debuggable"):
        assert expected in marked, expected


def test_gradient_findings_are_not_reflexes():
    """Things a reader would reasonably weigh against something else."""
    marked = set(rules.reflexes())
    for gradual in ("debt-markers", "long-functions", "no-ci", "readme",
                    "rust-unwrap", "new-code", "docs-dir"):
        assert gradual not in marked, gradual


def test_a_reflex_carries_its_words_not_a_number():
    rows = [{"name": "proj", "sources": {"rule:secret-literal": 6.0},
             "keys": {"rule:secret-literal": {"credential-shaped literal": 6.0}}}]
    got = serve._reflexes(rows, {"rule:secret-literal"})
    assert got == [("proj", "credential-shaped literal")]


def test_non_reflex_sources_are_ignored():
    rows = [{"name": "proj", "sources": {"rule:debt-markers": 12.0},
             "keys": {"rule:debt-markers": {"lots of TODOs": 12.0}}}]
    assert serve._reflexes(rows, {"rule:secret-literal"}) == []


def test_synthesised_machine_reflexes_work_too():
    """`disk-y-low` exists only because a Y: drive does, so it can never be in
    the static rule table. The set is collected at absorption instead."""
    rows = [{"name": "machine", "sources": {"rule:disk-y-low": 24.0},
             "keys": {"rule:disk-y-low": {"Y: is 0.3% free": 24.0}}}]
    got = serve._reflexes(rows, {"rule:disk-y-low"})
    assert got == [("machine", "Y: is 0.3% free")]


def test_a_reflex_falls_back_to_its_id_when_wordless():
    rows = [{"name": "proj", "sources": {"rule:secret-literal": 6.0}, "keys": {}}]
    assert serve._reflexes(rows, {"rule:secret-literal"}) == [("proj", "secret-literal")]


def test_the_section_is_absent_when_nothing_fires():
    """Silence is the expected state. A tier that always has something in it
    would be a second ranking with a louder heading."""
    assert serve._reflexes([{"name": "p", "sources": {}, "keys": {}}], set()) == []


def test_only_a_genuinely_critical_volume_arcs():
    """Four volumes were low; one was at 0.3%. A gradient of low disks is a
    gradient — running out is an event."""
    import inspect
    src = inspect.getsource(rules.machine)
    assert "CRITICAL_FREE_PCT" in src
    assert "reflex=free < CRITICAL_FREE_PCT" in src
