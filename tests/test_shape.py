"""The import resolver.

A structural claim that is wrong is worse than none: "four regions depend on
this" changes what you do about it. The resolver is a heuristic on purpose, so
what is tested is that it gets the common shapes right and stays silent rather
than guessing when it cannot.
"""

import pytest

from rp import shape


@pytest.mark.parametrize("line,path,expected", [
    ("from rp.store import Field", "collect.py", "rp/store"),
    ("import rp.sensors", "x.py", "rp/sensors"),
    ("use crate::engine::jobs;", "src/ui/view.rs", "engine/jobs"),
    ("pub use crate::ui::theme;", "src/app.rs", "ui/theme"),
    ("mod pixels;", "src/engine/mod.rs", "pixels"),
    ("import 'package:app_ui/services/db.dart';", "lib/main.dart",
     "services/db"),
    ("import './widgets/button.dart';", "lib/home.dart", "widgets/button"),
    ("import com.acme.sequencer.NativeBridge", "MainActivity.kt",
     "com/acme/sequencer/NativeBridge"),
])
def test_module_candidates(line, path, expected):
    assert expected in shape._module_candidates(line, path)


@pytest.mark.parametrize("line,path", [
    ("import 'dart:async';", "lib/main.dart"),          # sdk, not ours
    ("import 'https://example.com/x.js';", "a.ts"),     # remote
    ("// import the thing first", "x.py"),              # comment
    ("use std::collections::HashMap;", "src/a.rs"),     # not crate-local
])
def test_module_candidates_stays_silent(line, path):
    assert shape._module_candidates(line, path) == []


def test_resolve_prefers_longest_suffix():
    index = {"rp/store": "rp/store.py", "store": "other/store.py"}
    assert shape._resolve("rp/store", index) == "rp/store.py"


def test_resolve_drops_leading_package_segments():
    """`package:app_ui/services/db` should still find lib/services/db.dart."""
    index = {"lib/services/db": "lib/services/db.dart",
             "services/db": "lib/services/db.dart",
             "db": "lib/services/db.dart"}
    assert shape._resolve("app_ui/services/db", index) == "lib/services/db.dart"


def test_resolve_returns_none_when_nothing_matches():
    """Guessing would put edges in the graph that are not in the code."""
    assert shape._resolve("some/external/lib", {"rp/store": "rp/store.py"}) is None


def test_analyse_builds_region_edges(monkeypatch):
    """End to end on a tiny synthetic repo: ui imports engine, so engine gains
    fan-in and ui gains a dependency."""
    files = ["src/ui/view.rs", "src/ui/panel.rs", "src/engine/jobs.rs"]
    grep = ("src/ui/view.rs:1:use crate::engine::jobs;\n"
            "src/ui/panel.rs:3:use crate::engine::jobs;\n")
    monkeypatch.setattr(shape, "git", lambda repo, *a: grep)

    class R:
        @staticmethod
        def route_and_key(p):
            rel = p.split("repo/", 1)[1]
            return ("proj/ui" if "/ui/" in rel else "proj/engine"), rel

    got = shape.analyse("repo", "proj", R(), files)
    assert got["fan_in"] == {"proj/engine": 1}
    assert got["depends"] == {"proj/ui": 1}


def test_analyse_ignores_self_edges(monkeypatch):
    """A region importing itself is not a dependency, it is a region."""
    files = ["src/ui/view.rs", "src/ui/panel.rs"]
    monkeypatch.setattr(shape, "git", lambda repo, *a:
                        "src/ui/view.rs:1:use crate::ui::panel;\n")

    class R:
        @staticmethod
        def route_and_key(p):
            return "proj/ui", p.split("repo/", 1)[1]

    got = shape.analyse("repo", "proj", R(), files)
    assert got["fan_in"] == {} and got["depends"] == {}


def test_analyse_skips_vendored_sources(monkeypatch):
    """Structure we did not write is not structure worth mapping."""
    files = ["third_party/vendorlib/AudioStream.h", "src/a.rs"]
    monkeypatch.setattr(shape, "git", lambda repo, *a:
                        "third_party/vendorlib/AudioStream.h:1:use crate::src::a;\n")

    class R:
        @staticmethod
        def route_and_key(p):
            return "proj", p

    assert shape.analyse("repo", "proj", R(), files)["fan_in"] == {}


def test_entry_points_found():
    files = ["src/main.rs", "src/lib.rs", "README.md"]

    class R:
        @staticmethod
        def route_and_key(p):
            return "proj", p

    got = shape.analyse("repo", "proj", R(), files)
    assert "src/main.rs" in got["entries"]


def test_no_sources_is_not_an_error():
    class R:
        @staticmethod
        def route_and_key(p):
            return "proj", p

    got = shape.analyse("repo", "proj", R(), ["README.md", "logo.png"])
    assert got == {"fan_in": {}, "depends": {}, "entries": []}
