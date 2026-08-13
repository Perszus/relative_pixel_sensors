"""The store's invariants.

Every test here corresponds to a bug that actually happened, not to a line of
code that wanted covering. The epoch sentinel and the backward read both shipped
and both were found by accident.
"""

import math

import pytest

from rp.vortex import EPSILON, N_RINGS, RING_HALFLIVES, Channel

DAY = 86400.0
T0 = 1_000_000.0  # an arbitrary non-zero epoch


def test_lazy_decay_matches_closed_form():
    """The one mathematical claim the whole cost argument rests on."""
    lam = math.log(2) / RING_HALFLIVES[1]
    events = [(T0 + i * 3607.0, 1.0 + i % 3) for i in range(40)]
    ch = Channel()
    for t, m in events:
        ch.add(t, m)

    probe = events[-1][0] + 5 * DAY
    expected = sum(m * math.exp(-lam * (probe - t)) for t, m in events)
    assert ch.read(probe)[1] == pytest.approx(expected, rel=1e-9)


def test_first_event_at_epoch_zero_is_not_mistaken_for_never():
    """`t = 0.0` was the sentinel for "never written". Epoch zero is a legal
    timestamp, so any channel whose first event landed there read as cold
    forever -- and faked a pass in the benchmark suite by satisfying an
    all-zeros assertion."""
    ch = Channel()
    ch.add(0.0, 4.0)
    assert ch.read(0.0)[1] == pytest.approx(4.0)
    assert ch.t is not None


def test_reading_the_past_cannot_surface_future_events():
    """A read before the last write used to include events that had not
    happened yet, because the decay term went negative."""
    ch = Channel()
    ch.add(T0, 5.0)
    ch.add(T0 + 1000.0, 5.0)
    assert ch.read(T0 + 500.0)[1] <= ch.read(T0 + 1000.0)[1] + 1e-12


def test_single_event_counted_once_per_ring():
    ch = Channel()
    ch.add(T0, 3.0)
    assert ch.read(T0) == pytest.approx([3.0] * N_RINGS)


def test_longer_rings_retain_more_after_dormancy():
    ch = Channel()
    ch.add(T0, 10.0)
    later = ch.read(T0 + 30 * DAY)
    assert all(later[i] <= later[i + 1] + 1e-12 for i in range(N_RINGS - 1))
    # The month ring's half-life is 30d, so it should be at half strength.
    assert later[3] == pytest.approx(5.0, rel=1e-6)


def test_backwards_clock_cannot_inflate():
    ch = Channel()
    ch.add(T0, 1.0)
    before = ch.read(T0)[1]
    ch.add(T0 - 5000.0, 1.0)  # clock jumped back
    assert ch.read(T0)[1] <= before + 1.0 + 1e-9


def test_long_dormancy_collapses_to_hard_zero():
    """Not a denormal crawl: cold must be exactly cold, or 'nothing here'
    becomes a float comparison."""
    ch = Channel()
    ch.add(T0, 100.0)
    assert ch.read(T0 + 50 * 365 * DAY) == [0.0] * N_RINGS
    assert EPSILON > 0


def test_mass_stays_bounded_over_long_runs():
    ch = Channel()
    t = T0
    for i in range(20_000):
        t = T0 + i * 600.0
        ch.add(t, 1.0)
    assert all(v < 1e7 for v in ch.read(t))


def test_constant_stream_gives_flat_rate_across_working_rings():
    """Rings at different half-lives must agree about a steady rate, or the
    temporal profile is measuring the ring rather than the workload."""
    ch = Channel()
    t = T0
    for i in range(int(3 * 365 * DAY / 3600)):
        t = T0 + i * 3600.0
        ch.add(t, 1.0)
    rates = [ch.rates(t)[i] for i in Channel.WORKING]
    assert max(rates) / min(rates) < 1.05


@pytest.mark.parametrize("pattern,expected", [
    ("burst", "spike"),
    ("steady", "sustained"),
    ("stopped", "cooling"),
    ("ramp", "warming"),
    ("nothing", "cold"),
])
def test_temporal_profiles_classify(pattern, expected):
    """Warming is the one no instantaneous value can express, and is the
    concrete thing the rings buy."""
    now = T0 + 200 * DAY
    ch = Channel()
    if pattern == "burst":
        for i in range(20):
            ch.add(now - 1800 + i, 1.0)
    elif pattern == "steady":
        for t in range(int(T0), int(now), 7200):
            ch.add(float(t), 1.0)
    elif pattern == "stopped":
        for t in range(int(T0), int(now - 20 * DAY), 3600):
            ch.add(float(t), 1.0)
    elif pattern == "ramp":
        t = now - 90 * DAY
        while t < now:
            frac = (t - (now - 90 * DAY)) / (90 * DAY)
            ch.add(t, 1.0)
            t += max(86400.0 * (1.0 - 0.97 * frac), 900.0)
    assert ch.profile(now) == expected


def test_standing_sources_do_not_overwrite_each_other():
    """Several sensors legitimately press on one channel. A single slot let
    whichever ran last erase the others."""
    ch = Channel()
    ch.set_level("ester_open", 4.0, {"a.py": 4.0})
    ch.set_level("todo", 1.5, {"b.py": 1.5})
    assert ch.level == pytest.approx(5.5)
    assert set(ch.standing_by_source()) == {"ester_open", "todo"}

    ch.set_level("ester_open", 0.0)  # finding closed
    assert ch.level == pytest.approx(1.5)
    assert "ester_open" not in ch.standing_by_source()


def test_standing_does_not_decay():
    """An unfixed bug does not get better by being ignored."""
    ch = Channel()
    ch.set_level("ester_open", 6.0)
    assert ch.magnitude(T0 + 365 * DAY) == pytest.approx(6.0)


def test_standing_alone_reads_as_standing_not_cold():
    """Pressure with no activity behind it is the highest-value quadrant; it
    must not be reported as quiet."""
    ch = Channel()
    ch.set_level("ester_open", 6.0)
    assert ch.profile(T0) == "standing"


def test_round_trip_preserves_levels_and_rings():
    ch = Channel()
    ch.add(T0, 2.0, key="x.py")
    ch.set_level("todo", 3.0, {"y.py": 3.0})
    back = Channel.from_json(ch.to_json())
    assert back.read(T0) == pytest.approx(ch.read(T0))
    assert back.level == pytest.approx(ch.level)
    assert back.standing_by_source() == ch.standing_by_source()


def test_top_keys_stay_bounded():
    """State must be O(regions), never O(files touched)."""
    ch = Channel()
    for i in range(500):
        ch.add(T0 + i, 1.0, key=f"file_{i}.py")
    assert len(ch.top) <= 8
