"""HISTORY probes.

The kind's own rule: a probe must state its window, because a count without its
horizon is a number nobody can compare to anything. The tests hold that, and
pin the two bugs that made the whole kind silent.
"""

import time

import pytest

from rp import history, probes

DAY = 86400.0


def _log(monkeypatch, commits):
    """commits: list of (age_days, subject, [files])."""
    now = time.time()
    raw = "".join(
        f"\x01{now - age * DAY:.0f}\x02{subject}\n" + "".join(f"{f}\n" for f in files)
        for age, subject, files in commits)
    history._walk.cache_clear()
    monkeypatch.setattr(history, "git", lambda root, *a: raw)


def test_log_parses_git_own_escapes(monkeypatch):
    """The format string uses `%x01`/`%x02`, which git expands. Writing Python
    escapes instead put a literal NUL into an argument, which cannot survive
    being passed to a process — every log came back empty and every probe in
    this kind reported zero, which reads as nothing being wrong."""
    _log(monkeypatch, [(1, "add a thing", ["src/a.py", "src/b.py"])])
    commits = history.log("repo", 90)
    assert len(commits) == 1
    assert commits[0].subject == "add a thing"
    assert commits[0].files == ("src/a.py", "src/b.py")


def test_window_filters_rather_than_rewalking(monkeypatch):
    _log(monkeypatch, [(5, "recent", ["a.py"]), (100, "old", ["b.py"])])
    assert len(history.log("repo", 30)) == 1
    assert len(history.log("repo", 180)) == 2


def test_fix_detection(monkeypatch):
    _log(monkeypatch, [(1, "fix the crash", ["a.py"]),
                       (2, "add a feature", ["b.py"])])
    commits = history.log("repo", 90)
    assert commits[0].is_fix
    assert not commits[1].is_fix


def test_revert_detection(monkeypatch):
    _log(monkeypatch, [(1, "Revert \"add a thing\"", ["a.py"])])
    assert history.log("repo", 90)[0].is_revert


def test_churn_acceleration_needs_a_prior_period(monkeypatch):
    """A project's first month is not an acceleration, and reporting it as one
    would make every new repository look like it was on fire."""
    _log(monkeypatch, [(2, "w", ["a.py"]), (3, "w", ["b.py"])])
    assert history.churn_acceleration("repo", 30) == 0.0


def test_churn_acceleration_rises_when_change_speeds_up(monkeypatch):
    recent = [(5, "w", ["a.py", "b.py", "c.py"]) for _ in range(4)]
    prior = [(45, "w", ["a.py"])]
    _log(monkeypatch, recent + prior)
    assert history.churn_acceleration("repo", 30) > 1.0


def test_churn_acceleration_falls_when_change_slows(monkeypatch):
    _log(monkeypatch, [(5, "w", ["a.py"])] +
        [(45, "w", ["a.py", "b.py", "c.py"]) for _ in range(4)])
    assert 0.0 < history.churn_acceleration("repo", 30) < 1.0


def test_fix_ratio_stays_quiet_on_thin_history(monkeypatch):
    """Three commits, all repairs, is not a 100% repair rate — it is not enough
    history to have a rate."""
    _log(monkeypatch, [(i, "fix it", ["a.py"]) for i in range(3)])
    assert history.fix_ratio("repo", 90) == 0.0


def test_hotspots_counts_repeatedly_touched_files(monkeypatch):
    _log(monkeypatch, [(i, "w", ["hot.py"]) for i in range(10)] +
        [(1, "w", ["cold.py"])])
    assert history.hotspots("repo", 90, 5) == 1.0


def test_fix_only_files(monkeypatch):
    """A file only ever touched to repair something is being patched, not
    developed."""
    _log(monkeypatch, [(1, "fix a", ["patched.py"]), (2, "fix b", ["patched.py"]),
                       (3, "add", ["healthy.py"]), (4, "fix c", ["healthy.py"])])
    assert history.fix_only_files("repo", 180) == 1.0


def test_big_commits(monkeypatch):
    _log(monkeypatch, [(1, "sweep", [f"f{i}.py" for i in range(50)]),
                       (2, "small", ["a.py"])])
    assert history.big_commits("repo", 90, 40) == 1.0


def test_probe_wrapper_reports_unknown_not_zero(monkeypatch):
    """A probe that cannot answer must not be indistinguishable from one that
    answered 'nothing here'."""
    def boom(*a, **k):
        raise RuntimeError("no")
    monkeypatch.setattr(history, "churn", boom)
    value = probes.PROBES["churn"]("repo", 90)
    assert value != value          # NaN — unknown, not clean


def test_every_history_rule_states_its_window():
    from rp import rules
    history_probes = {"churn", "churn_acceleration", "fix_ratio", "reverts",
                      "hotspots", "new_code_share", "big_commits",
                      "fix_only_files", "commits_in"}
    for rule in rules.RULES:
        if rule.probe in history_probes:
            assert rule.args, f"{rule.id} has no window"
            assert isinstance(rule.args[0], int), f"{rule.id} window is not a number"
