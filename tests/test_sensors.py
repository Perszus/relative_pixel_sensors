"""Sensor rules that are easy to get wrong and silent when they are.

Both of the rules here were violated in shipped code: vendored TODOs were
reported as the fleet's own debt, and a seventeen-day-old test cache was one
commit away from being published as live failures.
"""

import json
import os
import time

import pytest

from rp import sensors

DAY = 86400.0


@pytest.mark.parametrize("path,ours", [
    ("src/main.rs", True),
    ("lib/widgets/button.dart", True),
    ("android/third_party/oboe/AudioStream.h", False),
    ("node_modules/left-pad/index.js", False),
    ("archive/cleanup-2026/old.py", False),
    ("backups/src_backup_20260416/Main.kt", False),
    ("vendor/github.com/x/y.go", False),
    ("app/legacy/thing.ts", False),
])
def test_is_ours(path, ours):
    """Pressure from code we did not write is not actionable. Thirty-eight
    'debt markers in orobos' were all Google's, inside a vendored audio
    library -- not false, but about somebody else, which reads as actionable
    and is worse."""
    assert sensors.is_ours(path) is ours


def test_dominant_kind_ignores_headers():
    """Headers accompany an implementation language rather than being one, so
    counting them made a Kotlin/C++ Android app report as an 'h' project."""
    src = ["a.kt", "b.kt", "c.kt", "d.h", "e.h", "f.h", "g.h", "h.cpp"]
    assert sensors._dominant_kind(src) == "kt"


def test_is_test_recognises_the_usual_shapes():
    for p in ["tests/test_x.py", "test/foo_test.dart", "lib/x_test.go",
              "src/__tests__/y.ts", "spec/z.rb", "test_thing.py"]:
        assert sensors._is_test(p), p
    for p in ["src/main.py", "lib/latest.dart", "src/protest.rs"]:
        assert not sensors._is_test(p), p


def test_verdict_age_reports_missing_artifact_as_none(tmp_path):
    assert sensors.verdict_age(str(tmp_path), str(tmp_path / "nope.json")) is None


def test_stale_thresholds_are_a_measure_not_an_assertion():
    """The first version asked 'has the code moved at all since the verdict',
    which is true of every project always -- a test that cannot stay silent is
    a horoscope."""
    assert sensors.STALE_DAYS > 0
    assert sensors.STALE_COMMITS > 1


def test_tests_failing_ignores_a_verdict_older_than_the_code(tmp_path, monkeypatch):
    """Paranoia's pytest cache was seventeen days stale. Published naively that
    is five live failures; in fact it is an opinion about code that no longer
    exists."""
    cache = tmp_path / ".pytest_cache" / "v" / "cache"
    cache.mkdir(parents=True)
    lastfailed = cache / "lastfailed"
    lastfailed.write_text(json.dumps({"tests/test_a.py::t": True}), encoding="utf-8")
    old = time.time() - 30 * DAY
    os.utime(lastfailed, (old, old))

    monkeypatch.setattr(sensors, "verdict_age",
                        lambda repo, art: {"days": 30.0, "commits_since": 40})
    failing, note = sensors.tests_failing(str(tmp_path), None, time.time())
    assert failing == {}
    assert note["tests_verdict"].startswith("stale")


def test_tests_failing_reports_a_current_verdict(tmp_path, monkeypatch):
    cache = tmp_path / ".pytest_cache" / "v" / "cache"
    cache.mkdir(parents=True)
    (cache / "lastfailed").write_text(
        json.dumps({"tests/test_a.py::one": True, "tests/test_a.py::two": True,
                    "tests/test_b.py::three": True}), encoding="utf-8")

    monkeypatch.setattr(sensors, "verdict_age",
                        lambda repo, art: {"days": 0.1, "commits_since": 0})

    class R:
        @staticmethod
        def route_and_key(p):
            return "proj", p.rsplit("/", 1)[-1]

    failing, note = sensors.tests_failing(str(tmp_path), R(), time.time())
    # Two distinct files, three failing cases: the unit is the file.
    assert failing["proj"][0] == pytest.approx(2 * sensors.W_TEST_FAIL)
    assert "2 file(s) failing" in note["tests_verdict"]


def test_weights_are_all_positive():
    """A zero weight is a sensor that is wired up and silent, which is the
    hardest kind of failure to notice."""
    for name in dir(sensors):
        if name.startswith("W_"):
            value = getattr(sensors, name)
            if isinstance(value, (int, float)):
                assert value > 0, name


def test_registry_covers_every_declared_weight_area():
    """The spec sheet is the documentation. It going stale is how a sensor set
    becomes folklore."""
    names = {s.name for s in sensors.REGISTRY}
    assert {"commits", "fixes", "wip", "todo", "ester_open", "tests_failing"} <= names
    for spec in sensors.REGISTRY:
        assert spec.channel in ("R", "G", "B")
        assert spec.kind in ("event", "standing", "state")
        assert spec.clears, spec.name
