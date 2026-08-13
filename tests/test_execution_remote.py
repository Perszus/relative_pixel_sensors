"""The two kinds that are not parasitic, built so the probes are.

Both are split the same way: the expensive, unreliable half happens out of band
— a person runs a command, someone refreshes a snapshot — and the probe is a
cheap local read of what that left behind.

The tests are almost entirely about the same thing: that absence of evidence is
never reported as good evidence.
"""

import json
import os
import time

import pytest

from rp import probes, remote, verdict

DAY = 86400.0


def _runs(tmp_path, records, monkeypatch, head="abc"):
    d = tmp_path / ".rp"
    d.mkdir(exist_ok=True)
    (d / "runs.json").write_text(json.dumps(records), encoding="utf-8")
    verdict.clear_caches()
    monkeypatch.setattr(verdict, "_head_sha", lambda root: head)
    return str(tmp_path)


# --- execution --------------------------------------------------------------

def test_passing_run_at_this_commit(tmp_path, monkeypatch):
    root = _runs(tmp_path, [{"label": "build", "code": 0, "at": time.time(),
                             "head": "abc"}], monkeypatch)
    assert verdict.run_state(root, "build")[0] == "pass"
    assert probes.PROBES["run_failed"](root, "build") == 0.0


def test_failing_run_at_this_commit(tmp_path, monkeypatch):
    root = _runs(tmp_path, [{"label": "build", "code": 1, "at": time.time(),
                             "head": "abc"}], monkeypatch)
    assert probes.PROBES["run_failed"](root, "build") == 1.0


def test_never_run_is_unknown_not_pass(tmp_path, monkeypatch):
    """The whole point. A project nobody has built is not a project that
    builds."""
    root = _runs(tmp_path, [], monkeypatch)
    assert verdict.run_state(root, "build")[0] == "never"
    value = probes.PROBES["run_failed"](root, "build")
    assert value != value          # NaN


def test_receipt_from_another_commit_is_stale(tmp_path, monkeypatch):
    """A wrapper that stops being used writes nothing, and its last receipt
    goes on saying whatever it said. A green build from forty commits ago is
    not a green build."""
    root = _runs(tmp_path, [{"label": "build", "code": 0, "at": time.time(),
                             "head": "old-sha"}], monkeypatch, head="new-sha")
    assert verdict.run_state(root, "build")[0] == "stale"
    value = probes.PROBES["run_failed"](root, "build")
    assert value != value


def test_ancient_receipt_is_stale_even_at_the_same_commit(tmp_path, monkeypatch):
    root = _runs(tmp_path, [{"label": "build", "code": 0,
                             "at": time.time() - 60 * DAY, "head": "abc"}],
                 monkeypatch)
    assert verdict.run_state(root, "build")[0] == "stale"


def test_unverified_flags_stale_and_never(tmp_path, monkeypatch):
    root = _runs(tmp_path, [], monkeypatch)
    assert probes.PROBES["run_unverified"](root, "build") == 1.0
    root = _runs(tmp_path, [{"label": "build", "code": 0, "at": time.time(),
                             "head": "abc"}], monkeypatch)
    assert probes.PROBES["run_unverified"](root, "build") == 0.0


def test_the_latest_receipt_wins(tmp_path, monkeypatch):
    root = _runs(tmp_path, [
        {"label": "build", "code": 1, "at": time.time() - 100, "head": "abc"},
        {"label": "build", "code": 0, "at": time.time(), "head": "abc"},
    ], monkeypatch)
    assert verdict.run_state(root, "build")[0] == "pass"


def test_labels_do_not_bleed(tmp_path, monkeypatch):
    root = _runs(tmp_path, [{"label": "test", "code": 1, "at": time.time(),
                             "head": "abc"}], monkeypatch)
    assert verdict.run_state(root, "build")[0] == "never"
    assert verdict.run_state(root, "test")[0] == "fail"


# --- remote -----------------------------------------------------------------

def _snapshot(tmp_path, monkeypatch, packages, age_days=0.0):
    path = tmp_path / "advisories.json"
    path.write_text(json.dumps(
        {"at": time.time() - age_days * DAY, "packages": packages,
         "queried": len(packages)}), encoding="utf-8")
    monkeypatch.setattr(remote, "SNAPSHOT", str(path))
    return str(tmp_path)


def test_no_snapshot_is_unknown_not_clean(tmp_path, monkeypatch):
    """"Could not check" is not a pass, and for a security-shaped signal that
    distinction is the only thing that makes it safe to publish."""
    monkeypatch.setattr(remote, "SNAPSHOT", str(tmp_path / "absent.json"))
    assert not remote.usable()
    value = probes.PROBES["vulnerable_deps"](str(tmp_path), 0)
    assert value != value


def test_expired_snapshot_is_unknown(tmp_path, monkeypatch):
    """Advisories are published continuously, so an old snapshot cannot tell
    'nothing known' from 'nothing fetched'."""
    root = _snapshot(tmp_path, monkeypatch, {"requests": 2}, age_days=90)
    assert not remote.usable()
    value = probes.PROBES["vulnerable_deps"](root, 0)
    assert value != value


def test_fresh_snapshot_answers(tmp_path, monkeypatch):
    root = _snapshot(tmp_path, monkeypatch, {"requests": 2})
    (tmp_path / "requirements.txt").write_text("requests==2.0.0\nrich\n",
                                               encoding="utf-8")
    assert remote.usable()
    assert probes.PROBES["vulnerable_deps"](root, 0) == 1.0


def test_clean_dependencies_report_zero_not_unknown(tmp_path, monkeypatch):
    """A checked-and-clean answer is real information and must be
    distinguishable from an unchecked one."""
    root = _snapshot(tmp_path, monkeypatch, {"something-else": 1})
    (tmp_path / "requirements.txt").write_text("rich\n", encoding="utf-8")
    assert probes.PROBES["vulnerable_deps"](root, 0) == 0.0


def test_the_probe_never_reaches_the_network(monkeypatch):
    """If the probe fetched, the field's availability would depend on somebody
    else's, and a timeout would read as clean."""
    def boom(*a, **k):
        raise AssertionError("a probe must not open a connection")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    probes.PROBES["vulnerable_deps"](".", 0)


def test_requirements_parsing():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "requirements.txt"), "w", encoding="utf-8") as fh:
            fh.write("requests==2.31.0\n# a comment\n-e .\nrich\nNumPy >= 1.0\n")
        got = remote._requirements(d)
    assert got["requests"] == "2.31.0"
    assert "rich" in got and "numpy" in got
    assert "-e" not in got
