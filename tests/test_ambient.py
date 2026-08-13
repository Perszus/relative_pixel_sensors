"""AMBIENT probes.

The kind's rule: these are LEVELS, not events. A value true now is meaningless
as history, so nothing here may be accumulated — a disk that was full an hour
ago and is fine now must read as fine.
"""

import json
import os
import time

import pytest

from rp import ambient, rules


def test_machine_findings_belong_to_the_machine():
    """"The system drive is full" is not a property of any project."""
    for f in rules.machine():
        assert f.subject == "machine"
        assert f.kind == "machine"


def test_memory_and_uptime_answer_or_say_zero():
    """A machine whose memory is unreadable is not a machine under pressure,
    so the honest fallback is zero rather than a guess."""
    assert 0.0 <= ambient.memory_used_pct() <= 100.0
    assert 0.0 <= ambient.commit_used_pct() <= 100.0
    assert ambient.uptime_days() >= 0.0


def test_self_stopping_services_are_not_failures():
    """Updaters and crash handlers register Automatic and exit. Counting them
    made eight 'failures' of which none was actionable, burying the ones that
    were."""
    for name in ("BraveElevationService", "edgeupdate", "GoogleUpdaterService",
                 "MicrosoftEdgeElevationService", "SysMain"):
        assert any(hint in name.lower() for hint in ambient._SELF_STOPPING), name


def test_real_service_names_are_not_filtered():
    for name in ("gpsvc", "sppsvc", "VMUSBArbService", "Spooler"):
        assert not any(hint in name.lower() for hint in ambient._SELF_STOPPING), name


def test_cache_sizes_are_read_from_the_ttl_cache(tmp_path, monkeypatch):
    """Measuring a 289 GB cache means walking it. Cache sizes move on the scale
    of days, so the answer is kept rather than recomputed every ten minutes —
    otherwise the probe stops being ambient and becomes a scan."""
    store = tmp_path / "sizes.json"
    store.write_text(json.dumps(
        {"models": {"gb": 250.0, "at": time.time()}}), encoding="utf-8")
    monkeypatch.setattr(ambient, "_SIZE_CACHE", str(store))

    walked = []
    monkeypatch.setattr(ambient, "_dir_size_gb",
                        lambda p: walked.append(p) or 1.0)
    monkeypatch.setattr(os.path, "isdir", lambda p: True)

    got = ambient.largest_caches({"models": str(tmp_path)}, 20.0)
    assert got == [("models", 250.0)]
    assert walked == [], "a fresh TTL entry must not trigger a walk"


def test_expired_cache_entry_is_recomputed(tmp_path, monkeypatch):
    store = tmp_path / "sizes.json"
    store.write_text(json.dumps(
        {"models": {"gb": 250.0, "at": time.time() - 10 * 3600}}), encoding="utf-8")
    monkeypatch.setattr(ambient, "_SIZE_CACHE", str(store))
    monkeypatch.setattr(ambient, "_dir_size_gb", lambda p: 42.0)
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    assert ambient.largest_caches({"models": str(tmp_path)}, 20.0) == [("models", 42.0)]


def test_caches_below_the_threshold_stay_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(ambient, "_SIZE_CACHE", str(tmp_path / "s.json"))
    monkeypatch.setattr(ambient, "_dir_size_gb", lambda p: 1.0)
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    assert ambient.largest_caches({"models": str(tmp_path)}, 20.0) == []


def test_resident_models_silent_when_nothing_answers(monkeypatch):
    """No inference server and no models loaded are the same thing for every
    purpose here."""
    def boom(*a, **k):
        raise OSError("refused")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    ambient.resident_models.cache_clear()
    assert ambient.resident_models() == ()


def test_every_volume_is_checked_not_a_hardcoded_pair():
    """The first version named C and F. A machine has whatever drives it has,
    and the one that was actually at 0.3% was Y."""
    import inspect
    src = inspect.getsource(rules.machine)
    assert "ascii_uppercase" in src
