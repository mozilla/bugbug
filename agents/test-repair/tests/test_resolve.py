import pytest
from hackbot_agents.test_repair import resolve
from hackbot_agents.test_repair.resolve import FailingGroup

GROUP = "dom/base/test/mochitest.ini"
TESTS = ["dom/base/test/test_a.js"]
PLATFORM = "linux1804-64-qr/debug"


def _investigation(**kwargs):
    defaults = dict(
        project="autoland",
        hg_revision="hgrev",
        harness="mochitest",
        platform=PLATFORM,
        failing_groups=[],
        commit_range=resolve.CommitRange("head", 1),
    )
    return resolve.Investigation(**{**defaults, **kwargs})


def _hg2git(rev):
    return {"hgA": "gitA", "hgB": "gitB", "hgC": "gitC"}.get(rev)


def test_resolve_range_widens_to_ancestor_pushes(monkeypatch):
    # The head push alone was one l10n commit, which both hid the real culprit and
    # left the shallow clone with nothing to enumerate.
    urls = []

    def fake_get_json(url):
        urls.append(url)
        if "changeset=hgA" in url:
            return {"pushes": {"500": {"changesets": [{"node": "hgA"}]}}}
        return {
            "pushes": {
                str(i): {"changesets": [{"node": f"hg{i}"}, {"node": f"hg{i}b"}]}
                for i in range(481, 501)
            }
        }

    monkeypatch.setattr(resolve, "_get_json", fake_get_json)
    monkeypatch.setattr(resolve, "_hg_to_git", _hg2git)
    rng = resolve._resolve_range("autoland", "hgA", 100)
    assert f"startID={500 - resolve.RANGE_PUSHES}&endID=500" in urls[1]
    assert rng.head == "gitA"
    assert rng.span == 40


def test_resolve_range_window_is_capped(monkeypatch):
    pushes = {str(i): {"changesets": [{"node": f"hg{i}"}]} for i in range(1, 40)}
    monkeypatch.setattr(resolve, "_get_json", lambda url: {"pushes": pushes})
    monkeypatch.setattr(resolve, "_hg_to_git", _hg2git)
    rng = resolve._resolve_range("autoland", "hgA", 10)
    # The span drives the clone depth, so the cap has to hold here too.
    assert rng.span == 10


def test_resolve_range_keeps_the_head_push_when_widening_fails(monkeypatch):
    def fake_get_json(url):
        if "changeset=hgA" in url:
            return {"pushes": {"500": {"changesets": [{"node": "hgA"}]}}}
        raise resolve.requests.exceptions.RequestException("hg down")

    monkeypatch.setattr(resolve, "_get_json", fake_get_json)
    monkeypatch.setattr(resolve, "_hg_to_git", _hg2git)
    rng = resolve._resolve_range("autoland", "hgA", 100)
    assert rng.head == "gitA"
    assert rng.span == 1


def test_resolve_range_none_when_head_unresolvable(monkeypatch):
    monkeypatch.setattr(resolve, "_hg_to_git", lambda rev: None)
    assert resolve._resolve_range("autoland", "hgHEAD", 100) is None


def test_resolve_range_survives_pushlog_failure(monkeypatch):
    def boom(url):
        raise resolve.requests.exceptions.RequestException("hg down")

    monkeypatch.setattr(resolve, "_get_json", boom)
    monkeypatch.setattr(resolve, "_hg_to_git", lambda rev: "gitHEAD")
    rng = resolve._resolve_range("autoland", "hgHEAD", 100)
    assert rng.head == "gitHEAD"
    assert rng.span == 1


def test_resolve_investigation_assembles_context(monkeypatch):
    task = {
        "tags": {
            "project": "autoland",
            "test-suite": "mochitest-browser-chrome",
            "test-platform": "linux1804-64-qr/debug",
        },
        "payload": {"env": {"GECKO_HEAD_REV": "hghead"}},
    }
    monkeypatch.setattr(resolve, "_get_json", lambda url: task)
    monkeypatch.setattr(
        resolve, "_failing_groups", lambda tid: [FailingGroup(GROUP, TESTS)]
    )
    monkeypatch.setattr(
        resolve, "_resolve_range", lambda *a: resolve.CommitRange("gitHead", 2)
    )

    inv = resolve.resolve_investigation("TASK")
    assert inv.project == "autoland"
    assert inv.hg_revision == "hghead"
    assert inv.harness == "mochitest"
    assert inv.platform == "linux1804-64-qr/debug"
    assert inv.debug_build is True
    assert inv.failure_commit == "gitHead"
    assert inv.commit_range.span == 2


def test_resolve_investigation_anchors_group_less_suites_on_the_task(monkeypatch):
    label = "test-macosx1500-aarch64/opt-gtest-1proc"
    task = {
        "tags": {
            "project": "autoland",
            "test-suite": "gtest",
            "test-platform": "macosx1500-aarch64/opt",
            "label": label,
        },
        "payload": {"env": {"GECKO_HEAD_REV": "hghead"}},
    }
    monkeypatch.setattr(resolve, "_get_json", lambda url: task)

    def no_group_lookup(task_id):
        raise AssertionError("group lookup must be skipped for a group-less suite")

    monkeypatch.setattr(resolve, "_failing_groups", no_group_lookup)
    monkeypatch.setattr(
        resolve, "_resolve_range", lambda *a: resolve.CommitRange("gitHead", 2)
    )

    inv = resolve.resolve_investigation("TASK")
    assert inv.group_based is False
    assert inv.failing_groups == []
    assert inv.label == label


def test_resolve_investigation_requires_a_git_commit(monkeypatch):
    task = {
        "tags": {"project": "autoland"},
        "payload": {"env": {"GECKO_HEAD_REV": "hghead"}},
    }
    monkeypatch.setattr(resolve, "_get_json", lambda url: task)
    monkeypatch.setattr(resolve, "_failing_groups", lambda tid: [])
    monkeypatch.setattr(resolve, "_resolve_range", lambda *a: None)
    with pytest.raises(ValueError):
        resolve.resolve_investigation("TASK")


def test_resolve_investigation_requires_revision(monkeypatch):
    monkeypatch.setattr(resolve, "_get_json", lambda url: {"tags": {}, "payload": {}})
    with pytest.raises(ValueError):
        resolve.resolve_investigation("TASK")


def _suggestion(line, bugs, resolution="", keywords="intermittent-failure"):
    return {
        "search": line,
        "bugs": {
            "open_recent": [
                {"id": bug, "resolution": resolution, "keywords": keywords}
                for bug in bugs
            ]
        },
    }


def test_known_intermittent_bugs_reads_treeherder(monkeypatch):
    calls = []

    def fake_get(url):
        calls.append(url)
        if "jobs/?task_id=" in url:
            return {"results": [{"id": 42}]}
        return [
            _suggestion("TEST-UNEXPECTED-FAIL | a.html | boom", [2016093]),
            # Same bug on a second line must not be listed twice.
            _suggestion("TEST-UNEXPECTED-TIMEOUT | a.html | hang", [2016093]),
            # Not a harness failure line: matches junk bugs on nearly every job.
            _suggestion("[taskcluster:error] exit status 1", [111111]),
            # Resolved, and keyword-less: neither is evidence of a known flake.
            _suggestion(
                "TEST-UNEXPECTED-FAIL | b.html | x", [222222], resolution="FIXED"
            ),
            _suggestion(
                "TEST-UNEXPECTED-FAIL | c.html | y", [333333], keywords="regression"
            ),
        ]

    monkeypatch.setattr(resolve, "_get_json", fake_get)
    assert resolve._known_intermittent_bugs("autoland", "TASK") == [2016093]
    assert "bug_suggestions" in calls[-1]


def test_known_intermittent_bugs_without_a_job(monkeypatch):
    monkeypatch.setattr(resolve, "_get_json", lambda url: {"results": []})
    assert resolve._known_intermittent_bugs("autoland", "TASK") == []


def test_known_intermittent_bugs_survive_a_treeherder_error(monkeypatch):
    def boom(url):
        raise resolve.requests.exceptions.RequestException("down")

    monkeypatch.setattr(resolve, "_get_json", boom)
    assert resolve._known_intermittent_bugs("autoland", "TASK") == []


def test_sheriff_classification_names_the_verdict(monkeypatch):
    monkeypatch.setattr(
        resolve,
        "_get_json",
        lambda url: {"results": [{"failure_classification_id": 2}]},
    )
    assert resolve.sheriff_classification("autoland", "TASK") == "fixed by commit"


def test_an_unclassified_job_was_not_actioned(monkeypatch):
    monkeypatch.setattr(
        resolve,
        "_get_json",
        lambda url: {"results": [{"failure_classification_id": 6}]},
    )
    assert resolve.sheriff_classification("autoland", "TASK") is None


def test_sheriff_classification_survives_a_treeherder_error(monkeypatch):
    def boom(url):
        raise resolve.requests.exceptions.RequestException("down")

    monkeypatch.setattr(resolve, "_get_json", boom)
    assert resolve.sheriff_classification("autoland", "TASK") is None
