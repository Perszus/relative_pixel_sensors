"""Fleet discovery.

A label collision here does not fail loudly: two repos merge into one set of
regions and the field reports one project's pressure under another's name.
"""

import collect


def test_labels_are_unique(monkeypatch, tmp_path):
    """`ester_code_slim` is aliased to "ester" and a directory literally called
    "ester" exists next to it."""
    root = tmp_path / "Development"
    for name in ("ester", "ester_code_slim", "veil"):
        (root / name / ".git").mkdir(parents=True)

    monkeypatch.setattr(collect, "FLEET_ROOTS", (str(root).replace("\\", "/"),))
    monkeypatch.setattr(collect, "git", lambda repo, *a: "abc123\n")

    fleet = collect.discover_fleet()
    labels = list(fleet.values())
    assert len(labels) == len(set(labels)), labels
    assert set(labels) == {"ester", "ester_code_slim", "veil"}


def test_aliases_apply_when_there_is_no_collision(monkeypatch, tmp_path):
    root = tmp_path / "Development"
    (root / "ouroborous_android" / ".git").mkdir(parents=True)
    monkeypatch.setattr(collect, "FLEET_ROOTS", (str(root).replace("\\", "/"),))
    monkeypatch.setattr(collect, "git", lambda repo, *a: "abc123\n")
    assert list(collect.discover_fleet().values()) == ["orobos"]


def test_repos_without_commits_are_skipped(monkeypatch, tmp_path):
    """An empty repo has nothing to say and would only add a permanently cold
    region."""
    root = tmp_path / "Development"
    (root / "empty" / ".git").mkdir(parents=True)
    monkeypatch.setattr(collect, "FLEET_ROOTS", (str(root).replace("\\", "/"),))
    monkeypatch.setattr(collect, "git", lambda repo, *a: "")
    assert collect.discover_fleet() == {}


def test_non_repo_directories_are_ignored(monkeypatch, tmp_path):
    root = tmp_path / "Development"
    (root / "just_a_folder").mkdir(parents=True)
    monkeypatch.setattr(collect, "FLEET_ROOTS", (str(root).replace("\\", "/"),))
    monkeypatch.setattr(collect, "git", lambda repo, *a: "abc123\n")
    assert collect.discover_fleet() == {}


def test_edited_fleet_json_wins_over_discovery(monkeypatch, tmp_path):
    """Pruning a repo you deliberately do not want watched has to stick."""
    cfg = tmp_path / "fleet.json"
    cfg.write_text('{"F:/Code/Development/veil": "veil"}', encoding="utf-8")
    monkeypatch.setattr(collect, "FLEET_CFG", str(cfg))
    monkeypatch.setattr(collect, "discover_fleet",
                        lambda: {"F:/Code/Development/other": "other"})
    assert collect.load_fleet() == {"F:/Code/Development/veil": "veil"}
    assert collect.load_fleet(rediscover=True) == {"F:/Code/Development/other": "other"}


def test_project_health_propagates_to_regions():
    """"This repo has CI" is true of every directory in it. Without this, 34 of
    45 regions read "nothing has vouched for them" -- a scoping gap in the
    sensor, reported as a fact about the code."""
    got = collect._inherit({"veil": (4.0, {"ci": 1.0})},
                           {"veil", "veil/lib", "veil/lib/deep", "orobos"})
    assert got["veil/lib"] == (4.0, {"ci": 1.0})
    assert got["veil/lib/deep"] == (4.0, {"ci": 1.0})
    assert "orobos" not in got


def test_propagation_does_not_leak_across_similarly_named_projects():
    """`veil` must not claim `veilish`."""
    got = collect._inherit({"veil": (4.0, {})}, {"veil", "veilish", "veil/lib"})
    assert "veilish" not in got
    assert "veil/lib" in got
