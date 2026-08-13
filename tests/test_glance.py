"""The orientation layer.

`glance` is the only output that is paid whether or not anyone wanted it: a
host runs it at the start of every session and the result lands in a reader's
context unasked. That changes what correctness means here. The brief may be
long because someone chose to open it; this may not. The brief may raise
because a human is watching; this may not.

So the tests are about the guarantees a always-on payload has to make —
bounded size, no exceptions, no dependence on the field being fresh, present,
or even parseable — rather than about the content being interesting.
"""

import json

import pytest

from rp.serve import _ago, glance

HOUR = 3600.0
DAY = 86400.0
NOW = 1_000_000.0


def view(groups, at=NOW):
    return {"collected_at": at, "groups": groups}


def region(name, r=0.0, g=0.0, b=0.0, reflexes=()):
    return {"name": name, "rgb": [r, g, b], "reflexes": list(reflexes)}


# --- staleness: the one thing the reader asked to always be told ------------

def test_age_is_stated_before_anything_else():
    """The reading is fed by a tray app that is sometimes closed. An age that
    has to be looked for is an age that will be assumed."""
    out = glance(view([region("a", r=5.0)], at=NOW - 600), "P", now=NOW)
    assert "10 min ago" in out.splitlines()[0]


def test_fresh_field_is_not_marked_stale():
    out = glance(view([region("a", r=5.0)], at=NOW - 60), "P", now=NOW)
    assert "STALE" not in out


def test_stale_field_says_so_loudly():
    out = glance(view([region("a", r=5.0)], at=NOW - 9 * HOUR), "P", now=NOW)
    assert "STALE" in out
    assert "collect.py" in out


def test_staleness_threshold_is_the_boundary_not_a_mood():
    fresh = glance(view([region("a", r=5.0)], at=NOW - 5.9 * HOUR), "P", now=NOW)
    stale = glance(view([region("a", r=5.0)], at=NOW - 6.1 * HOUR), "P", now=NOW)
    assert "STALE" not in fresh
    assert "STALE" in stale


def test_missing_collected_at_reads_as_ancient_not_as_fresh():
    """A view with no timestamp is the epoch-0 trap: absent must not be able to
    masquerade as current, which is the direction that gets acted on."""
    out = glance({"groups": [region("a", r=5.0)]}, "P", now=NOW)
    assert "STALE" in out


def test_clock_skew_does_not_produce_a_negative_age():
    out = glance(view([region("a", r=5.0)], at=NOW + 300), "P", now=NOW)
    assert "-" not in out.splitlines()[0]
    assert "just now" in out.splitlines()[0]


@pytest.mark.parametrize("seconds,expect", [
    (0, "just now"), (89, "just now"), (600, "10 min ago"),
    (2 * HOUR, "2 h ago"), (3 * DAY, "3 days ago"),
])
def test_ago_reads_at_the_scale_of_the_gap(seconds, expect):
    assert _ago(seconds) == expect


# --- reflexes: never summarised --------------------------------------------

def test_every_reflex_is_shown_in_full():
    groups = [region(f"p{i}", r=1.0, reflexes=[f"finding {i}"]) for i in range(9)]
    out = glance(view(groups), "P", now=NOW)
    for i in range(9):
        assert f"finding {i}" in out


def test_reflexes_are_not_subject_to_the_stalled_limit():
    """`limit` truncates the stalled list. It must not reach the tier that
    exists precisely because it cannot wait for a second look."""
    groups = [region(f"p{i}", r=9.0, reflexes=[f"finding {i}"]) for i in range(8)]
    out = glance(view(groups), "P", now=NOW, limit=2)
    assert sum("finding" in ln for ln in out.splitlines()) == 8


def test_quiet_field_shows_no_reflex_lines():
    out = glance(view([region("a", r=3.0), region("b", b=40.0)]), "P", now=NOW)
    assert "!!" not in out


# --- stalled: names, deliberately without numbers ---------------------------

def test_stalled_is_pressure_without_activity():
    groups = [
        region("abandoned", r=8.0, b=0.0),
        region("worked-on", r=8.0, b=30.0),
    ]
    out = glance(view(groups), "P", now=NOW)
    assert "abandoned" in out
    assert "worked-on" not in out


def test_stalled_carries_no_magnitudes():
    """A number with no context invites being acted on. The brief supplies the
    context; this layer's job is only to say where to point."""
    out = glance(view([region("hot", r=54.2, b=0.0)]), "P", now=NOW)
    assert "54" not in out


def test_stalled_is_ordered_by_pressure_and_truncated_with_a_count():
    groups = [region(f"p{i}", r=float(i)) for i in range(1, 12)]
    out = glance(view(groups), "P", now=NOW, limit=3)
    line = next(ln for ln in out.splitlines() if "stalled" in ln)
    assert line.index("p11") < line.index("p10") < line.index("p9")
    assert "(+7 more)" in line


# --- the guarantees of a payload nobody asked for ---------------------------

def test_output_is_ascii_even_when_rules_are_not():
    """Crosses an unknown shell. Rule text is not trusted to be ASCII."""
    groups = [region("p", r=5.0, reflexes=["curly — dash · dot"])]
    out = glance(view(groups), "P", now=NOW)
    out.encode("ascii")


def test_stays_small_on_a_large_and_angry_field():
    """Paid every session forever. Size must be a function of the limits, not
    of how bad things have got."""
    groups = [region(f"proj/region/{i}", r=float(i % 50)) for i in range(2000)]
    out = glance(view(groups), "P", now=NOW)
    assert len(out) < 700, len(out)


def test_empty_field_says_what_to_do_about_it():
    out = glance({"collected_at": NOW, "groups": []}, "P/here", now=NOW)
    assert "collect.py" in out and "P/here" in out


def test_never_raises_on_a_malformed_view():
    """Anything that can reach this has already been written by a collector,
    so a shape error means the collector changed -- which must degrade the
    session, not end it."""
    for broken in ({}, {"groups": None}, {"groups": []}):
        assert isinstance(glance(broken, "P", now=NOW), str)


def test_always_points_at_the_next_step():
    out = glance(view([region("a", r=5.0)]), "P", now=NOW)
    assert "BRIEF.txt" in out


def test_pointer_survives_a_completely_quiet_fleet():
    """The escalation path must not be a side effect of something being wrong."""
    out = glance(view([region("a"), region("b")]), "P", now=NOW)
    assert "BRIEF.txt" in out


def test_counts_separate_projects_from_regions():
    groups = [region("alpha"), region("alpha/src"), region("beta"),
              region("machine", r=5.0)]
    out = glance(view(groups), "P", now=NOW)
    assert "2 projects" in out
    assert "4 regions" in out


def test_reads_a_real_view_shape():
    """Guards the contract with `write_view`: this layer reads the rendered
    view, so a key rename there must fail here rather than silently at 3am."""
    payload = json.loads(json.dumps(view([
        region("machine", r=54.2, reflexes=["Y: is 0.3% free"]),
        region("proj/lib", r=13.0),
    ])))
    out = glance(payload, "P", now=NOW)
    assert "Y: is 0.3% free" in out and "proj/lib" in out
