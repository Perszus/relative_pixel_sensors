"""Routing and clearing.

Clearing is the half that gets forgotten, and forgetting it does not fail
loudly -- the field simply keeps reporting things that stopped being true.
"""

import pytest

from rp.store import UNROUTED, Field, Router, _regions

T0 = 1_000_000.0


def test_longest_prefix_wins():
    r = Router([("F:/Code/proj", "proj"), ("F:/Code/proj/src", "proj/src")])
    assert r.route("F:/Code/proj/src/main.py") == "proj/src"
    assert r.route("F:/Code/proj/README.md") == "proj"


def test_keys_are_relative_to_their_group():
    """The absolute prefix is implied by the group name; repeating it in every
    pointer was the largest single term in resident size."""
    r = Router([("F:/Code/proj/src", "proj/src")])
    group, key = r.route_and_key("F:/Code/proj/src/deep/main.py")
    assert (group, key) == ("proj/src", "deep/main.py")


def test_unrouted_is_counted_never_silently_dropped():
    """Cold has to mean "nothing happened", not "nothing was routed"."""
    f = Field(Router([("F:/Code/proj", "proj")]))
    f.emit("F:/Somewhere/else.py", "R", 1.0, now=T0)
    assert f.dropped == 1
    assert UNROUTED in f.groups


def test_separators_and_case_do_not_change_routing():
    r = Router([("F:/Code/Proj", "proj")])
    assert r.route("F:\\Code\\proj\\src\\main.py") == "proj"


# --- adaptive region sizing -------------------------------------------------

def test_big_directory_is_split():
    files = [f"src/mod{i // 5}/f{i}.py" for i in range(40)]
    regions = _regions(files, max_files=12, min_files=3, max_depth=5)
    assert any(r.startswith("src/mod") for r in regions)


def test_small_directory_folds_into_parent():
    """A region too thin to be worth naming should not become one."""
    files = ["src/a.py", "src/b.py", "src/tiny/x.py"] + \
            [f"src/big/f{i}.py" for i in range(20)]
    regions = _regions(files, max_files=5, min_files=3, max_depth=5)
    assert "src/tiny" not in regions
    assert any(r.startswith("src/big") for r in regions)


def test_flat_project_yields_one_region():
    files = [f"f{i}.py" for i in range(3)]
    assert _regions(files, max_files=12, min_files=3, max_depth=5) == []


def test_parent_keeps_a_region_when_files_stay_behind():
    """Loose files at a level still need somewhere to land."""
    files = ["src/loose.py"] + [f"src/deep/f{i}.py" for i in range(20)]
    regions = _regions(files, max_files=5, min_files=3, max_depth=5)
    assert "src" in regions
    assert "src/deep" in regions


# --- clearing ---------------------------------------------------------------

def test_apply_state_retracts_from_groups_no_longer_reported():
    """The finding was fixed, or the file moved. Either way the old group has
    to stop pressing, and it can only do that if something notices the absence."""
    f = Field()
    f.emit("x", "R", 1.0, now=T0, group="a")
    f.emit("x", "R", 1.0, now=T0, group="b")

    f.apply_state("R", "ester_open", {"a": (4.0, {"f.py": 4.0}), "b": (2.0, {})})
    assert f.groups["a"].channels["R"].level == pytest.approx(4.0)
    assert f.groups["b"].channels["R"].level == pytest.approx(2.0)

    # Next pass: only "a" still has findings.
    f.apply_state("R", "ester_open", {"a": (4.0, {"f.py": 4.0})})
    assert f.groups["a"].channels["R"].level == pytest.approx(4.0)
    assert f.groups["b"].channels["R"].level == pytest.approx(0.0)


def test_apply_state_leaves_other_sensors_alone():
    f = Field()
    f.emit("x", "R", 1.0, now=T0, group="a")
    f.apply_state("R", "todo", {"a": (1.5, {})})
    f.apply_state("R", "ester_open", {})  # this sensor found nothing
    assert f.groups["a"].channels["R"].level == pytest.approx(1.5)


def test_apply_state_creates_groups_it_has_never_seen():
    f = Field()
    f.apply_state("R", "todo", {"brand/new": (2.0, {})})
    assert f.groups["brand/new"].channels["R"].level == pytest.approx(2.0)


# --- persistence ------------------------------------------------------------

def test_save_load_round_trip(tmp_path):
    f = Field()
    f.emit("F:/p/a.py", "B", 2.0, now=T0, group="p")
    f.apply_state("R", "todo", {"p": (1.5, {"a.py": 1.5})})
    path = str(tmp_path / "field.json")
    f.save(path)

    back = Field.load(path)
    assert back.groups["p"].channels["B"].read(T0) == \
        pytest.approx(f.groups["p"].channels["B"].read(T0))
    assert back.groups["p"].channels["R"].level == pytest.approx(1.5)


def test_saved_field_records_when_it_was_collected(tmp_path):
    """An unfed field looks calm, and calm is what this thing uses to say
    'safe to ignore'."""
    import json
    f = Field()
    path = str(tmp_path / "field.json")
    f.save(path)
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["collected_at"] > 0


def test_read_cost_does_not_depend_on_history():
    """O(regions), never O(events)."""
    f = Field()
    for i in range(5000):
        f.emit("x", "R", 1.0, now=T0 + i, group="a")
    assert len(f.rank(T0 + 5000)) == 1
