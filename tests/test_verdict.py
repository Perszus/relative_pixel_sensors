"""VERDICT probes.

The kind's risk is staleness, and it is the most dangerous in the system
because the output looks perfectly current. Every test here is about refusing
to answer from expired evidence.
"""

import json
import os
import time

import pytest

from rp import probes, verdict

DAY = 86400.0


def _cache(tmp_path, lastfailed=None, nodeids=None, age_days=0.0,
           declares_pytest=True):
    if declares_pytest:
        # Evidence that pytest is the runner, not merely that it once ran.
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\n", encoding="utf-8")
    base = tmp_path / ".pytest_cache" / "v" / "cache"
    base.mkdir(parents=True)
    if lastfailed is not None:
        p = base / "lastfailed"
        p.write_text(json.dumps(lastfailed), encoding="utf-8")
        when = time.time() - age_days * DAY
        os.utime(p, (when, when))
    if nodeids is not None:
        (base / "nodeids").write_text(json.dumps(nodeids), encoding="utf-8")
    verdict.clear_caches()
    return str(tmp_path)


def test_totals_gives_a_denominator(tmp_path):
    """"Five files failing" and "five of six" are different situations."""
    root = _cache(tmp_path,
                  lastfailed={"tests/a.py::x": True, "tests/a.py::y": True,
                              "tests/b.py::z": True},
                  nodeids=[f"t{i}" for i in range(40)])
    assert verdict.test_totals(root) == (2, 40)   # two FILES, forty tests


def test_missing_cache_is_zeros_not_an_error(tmp_path):
    verdict.clear_caches()
    assert verdict.test_totals(str(tmp_path)) == (0, 0)


def test_a_cache_alone_does_not_make_it_a_pytest_project(tmp_path):
    """A `.pytest_cache` proves pytest was run here once, which is a different
    claim. One project in this fleet writes standalone test scripts, run
    directly and all passing, and carries a stray cache from a single
    wrong-runner invocation — whose collection crash the cache records as five
    failing files."""
    root = _cache(tmp_path, lastfailed={"tests/a.py::x": True}, nodeids=["t"],
                  declares_pytest=False)
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_thing.py").write_text(
        "import sys\nprint('checks')\nsys.exit(0)\n", encoding="utf-8")
    verdict.uses_pytest.cache_clear()
    assert verdict.uses_pytest(root) is False
    value = probes.PROBES["failing_test_share"](root, 0)
    assert value != value          # UNKNOWN, not five failures


def test_pytest_style_definitions_are_evidence_of_intent(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_thing.py").write_text(
        "def test_it():\n    assert True\n", encoding="utf-8")
    verdict.uses_pytest.cache_clear()
    assert verdict.uses_pytest(str(tmp_path)) is True


def test_a_pytest_config_section_is_evidence_of_intent(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n", encoding="utf-8")
    verdict.uses_pytest.cache_clear()
    assert verdict.uses_pytest(str(tmp_path)) is True


def test_stale_cache_reports_unknown_not_a_pass(tmp_path):
    """A stale pass is not a pass. Returning zero would say the suite is clean
    on the strength of a run that predates the code."""
    root = _cache(tmp_path, lastfailed={}, nodeids=["t1"], age_days=30)
    value = probes.PROBES["failing_test_share"](root, 0)
    assert value != value          # NaN


def test_fresh_cache_answers(tmp_path):
    root = _cache(tmp_path,
                  lastfailed={"tests/a.py::x": True},
                  nodeids=[f"t{i}" for i in range(10)],
                  age_days=0.0)
    assert probes.PROBES["failing_test_share"](root, 0) == pytest.approx(10.0)


def test_no_collected_tests_is_unknown(tmp_path):
    """Zero of zero is not zero percent failing."""
    root = _cache(tmp_path, lastfailed={}, nodeids=[], age_days=0.0)
    value = probes.PROBES["failing_test_share"](root, 0)
    assert value != value


# --- logs -------------------------------------------------------------------

def _log(tmp_path, lines, monkeypatch, last_commit=0.0):
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "app.log").write_text("\n".join(lines), encoding="utf-8")
    verdict.clear_caches()
    verdict._last_commit.cache_clear()
    monkeypatch.setattr(verdict, "_last_commit", lambda root: last_commit)
    return str(tmp_path)


def _stamp(days_ago):
    return time.strftime("%Y-%m-%d %H:%M:%S",
                         time.localtime(time.time() - days_ago * DAY))


def test_recent_errors_are_counted(tmp_path, monkeypatch):
    root = _log(tmp_path, [f"{_stamp(1)} ERROR everything broke"], monkeypatch)
    count, example = verdict.recent_log_errors(root, 7.0)
    assert count == 1 and "everything broke" in example


def test_old_errors_age_out(tmp_path, monkeypatch):
    """Counting every error line reports every problem ever solved as a current
    one."""
    root = _log(tmp_path, [f"{_stamp(30)} ERROR ancient history"], monkeypatch)
    assert verdict.recent_log_errors(root, 7.0)[0] == 0


def test_errors_predating_the_last_commit_are_not_live(tmp_path, monkeypatch):
    """An error written before the code changed is an opinion about older code.
    This project's own log held a GL panic from three days before the fix
    landed, and without this it would be reported as live indefinitely."""
    root = _log(tmp_path, [f"{_stamp(3)} PANIC crashed"], monkeypatch,
                last_commit=time.time() - 1 * DAY)
    assert verdict.recent_log_errors(root, 7.0)[0] == 0


def test_a_line_saying_zero_errors_is_not_an_error(tmp_path, monkeypatch):
    root = _log(tmp_path, [f"{_stamp(1)} build finished with 0 errors"],
                monkeypatch)
    assert verdict.recent_log_errors(root, 7.0)[0] == 0


def test_undated_lines_use_the_file_mtime(tmp_path, monkeypatch):
    root = _log(tmp_path, ["ERROR no timestamp here"], monkeypatch)
    assert verdict.recent_log_errors(root, 7.0)[0] == 1

    old = time.time() - 30 * DAY
    os.utime(os.path.join(root, "logs", "app.log"), (old, old))
    verdict.clear_caches()
    assert verdict.recent_log_errors(root, 7.0)[0] == 0
