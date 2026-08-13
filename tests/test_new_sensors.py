"""Sensors added after the first sixteen.

The secrets sensor gets the most attention here because its first version was
100% false positives across six repos, and a sensor that cries wolf is worse
than an absent one — it teaches the reader to skip the section.
"""

import pytest

from rp import sensors, shape


# --- secrets ----------------------------------------------------------------

@pytest.mark.parametrize("line", [
    'token = credentials.credentials',
    'private val _hasPassword = MutableStateFlow(PasswordManager.hasPassword(app))',
    'final isAwaitingPassword = state.pendingUnlockNoteId != null &&',
    'access_token = create_access_token(user["user_id"], timedelta(minutes=30))',
])
def test_secret_regex_ignores_identifiers(line):
    """All four of these were reported as secrets by the first version. None
    of them contains a credential; they assign from a call or a variable."""
    assert sensors._SECRET_RE.search(line) is None


@pytest.mark.parametrize("value,is_secret", [
    ("xxxxxxxxxxxxxxxxxxxxxxxx", False),      # placeholder
    ("your_api_key_goes_here__", False),      # placeholder
    ("CHANGEME_CHANGEME_CHANGE", False),      # placeholder-ish, low entropy
    ("aaaaaaaaaaaaaaaaaaaaaaaa", False),      # no entropy
    ("1.2.3.4.5.6.7.8.9.10.11.1", False),     # version-ish
    ("sk_live_9Fq2XbTn4vKpZ7wLmR3d", True),   # looks like the real thing
])
def test_placeholder_and_entropy_filters(value, is_secret):
    rejected = (sensors._NOT_A_SECRET.match(value) is not None
                or sensors._entropy(value) < 3.2)
    assert rejected == (not is_secret), f"{value} entropy={sensors._entropy(value):.2f}"


def test_entropy_separates_random_from_structured():
    assert sensors._entropy("aaaaaaaa") < 1.0
    assert sensors._entropy("sk_live_9Fq2XbTn4vKpZ7wLmR3d") > 3.5


def test_secret_regex_matches_a_real_literal():
    # rp:allow secret-literal — a fixture for the detector, not a credential.
    m = sensors._SECRET_RE.search('api_key = "sk_live_9Fq2XbTn4vKpZ7wLmR3d"')
    assert m and m.group(2) == "sk_live_9Fq2XbTn4vKpZ7wLmR3d"


# --- doc drift --------------------------------------------------------------

def test_doc_drift_silent_without_a_readme():
    assert sensors.doc_drift("repo", "proj", ["src/main.rs"]) == {}


def test_doc_drift_silent_when_barely_behind(monkeypatch):
    monkeypatch.setattr(sensors, "git", lambda repo, *a:
                        "1700000000" if "log" in a else "10")
    assert sensors.doc_drift("repo", "proj", ["README.md"]) == {}


def test_doc_drift_scales_and_caps(monkeypatch):
    def fake(repo, *a):
        return "1700000000" if "log" in a else "400"
    monkeypatch.setattr(sensors, "git", fake)
    got = sensors.doc_drift("repo", "proj", ["README.md"])
    # Capped: a README 400 commits behind is not 16x worse than one 100 behind.
    assert got["proj"][0] == pytest.approx(sensors.W_DOC_DRIFT * 4.0)


# --- suggestions ------------------------------------------------------------

def test_suggestions_counts_only_open(tmp_path):
    (tmp_path / "ester_suggestions.md").write_text(
        "### [OPEN] a — sev 3\n### [DONE] b\n### [OPEN] c — sev 2\n",
        encoding="utf-8")
    got = sensors.suggestions(str(tmp_path), "proj")
    assert got["proj"][0] == pytest.approx(2 * sensors.W_SUGGESTION)


def test_suggestions_absent_is_silent(tmp_path):
    assert sensors.suggestions(str(tmp_path), "proj") == {}


def test_a_suggestion_weighs_less_than_a_finding():
    """An unacted-on good idea is not a defect and must not rank like one."""
    assert sensors.W_SUGGESTION < min(sensors.W_ESTER_SEV.values())


# --- co-change --------------------------------------------------------------

def test_co_change_ignores_files_in_the_same_directory(monkeypatch):
    """Files beside each other are expected to move together; saying so drowns
    the pairs that are surprising."""
    log = "@\nsrc/a.rs\nsrc/b.rs\n" * 10
    monkeypatch.setattr(shape, "git", lambda repo, *a: log)

    class R:
        @staticmethod
        def route_and_key(p):
            return "proj", p

    assert shape._co_change("repo", R()) == []


def test_co_change_finds_cross_directory_pairs(monkeypatch):
    log = "@\ncpp/engine.cpp\njava/Bridge.kt\n" * 8
    monkeypatch.setattr(shape, "git", lambda repo, *a: log)

    class R:
        @staticmethod
        def route_and_key(p):
            return "proj", p

    got = shape._co_change("repo", R())
    assert got and got[0][0] == 8
    assert {got[0][1], got[0][2]} == {"cpp/engine.cpp", "java/Bridge.kt"}


def test_co_change_ignores_sweeping_commits(monkeypatch):
    """A rename or a reformat couples everything to everything and is evidence
    of nothing."""
    files = "\n".join(f"d{i}/f{i}.py" for i in range(40))
    monkeypatch.setattr(shape, "git", lambda repo, *a: f"@\n{files}\n" * 10)

    class R:
        @staticmethod
        def route_and_key(p):
            return "proj", p

    assert shape._co_change("repo", R()) == []


def test_co_change_needs_repetition(monkeypatch):
    """Two files touched together once is a commit, not a relationship."""
    monkeypatch.setattr(shape, "git", lambda repo, *a:
                        "@\ncpp/a.cpp\njava/B.kt\n")

    class R:
        @staticmethod
        def route_and_key(p):
            return "proj", p

    assert shape._co_change("repo", R()) == []
