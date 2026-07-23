import pytest
from hackbot_agents.test_repair import resolve
from hackbot_agents.test_repair.resolve import FailingGroup
from mozci.errors import ParentPushNotFound
from mozci.task import Status

GROUP = "dom/base/test/mochitest.ini"


class FakeSummary:
    def __init__(self, status):
        self.status = status


class FakePush:
    def __init__(self, rev, summaries=None, parent=None):
        self.rev = rev
        self.group_summaries = summaries or {}
        self._parent = parent

    @property
    def parent(self):
        if self._parent is None:
            raise ParentPushNotFound(f"no parent for {self.rev}")
        return self._parent


def test_harness_detection():
    assert resolve._harness({"test-suite": "xpcshell"}) == "xpcshell"
    assert resolve._harness({"label": "test-linux/opt-xpcshell-4"}) == "xpcshell"
    assert resolve._harness({"test-suite": "mochitest-browser-chrome"}) == "mochitest"
    assert resolve._harness({"kind": "web-platform-tests"}) == "web-platform-tests"
    assert resolve._harness({}) == "unknown"


def test_last_green_returns_first_passing_ancestor(monkeypatch):
    green = FakePush("greenrev", {GROUP: FakeSummary(Status.PASS)})
    flaky = FakePush(
        "flakyrev", {GROUP: FakeSummary(Status.INTERMITTENT)}, parent=green
    )
    head = FakePush("headrev", {}, parent=flaky)
    monkeypatch.setattr(resolve, "Push", lambda rev, branch=None: head)
    assert resolve._last_green("autoland", "headrev", GROUP) == "greenrev"


def test_last_green_none_when_already_failing_upstream(monkeypatch):
    parent = FakePush("parentrev", {GROUP: FakeSummary(Status.FAIL)})
    head = FakePush("headrev", {}, parent=parent)
    monkeypatch.setattr(resolve, "Push", lambda rev, branch=None: head)
    assert resolve._last_green("autoland", "headrev", GROUP) is None


def test_last_green_fails_soft_on_error(monkeypatch):
    def boom(rev, branch=None):
        raise RuntimeError("mozci exploded")

    monkeypatch.setattr(resolve, "Push", boom)
    assert resolve._last_green("autoland", "headrev", GROUP) is None


def test_candidate_commits_range_head_first(monkeypatch):
    pushes = {
        "1": {"changesets": [{"node": "hgA"}], "git_changesets": ["gitA"]},
        "2": {
            "changesets": [{"node": "hgB"}, {"node": "hgC"}],
            "git_changesets": ["gitB", "gitC"],
        },
    }
    monkeypatch.setattr(resolve, "_get_json", lambda url: {"pushes": pushes})
    commits = resolve._candidate_commits("autoland", "hgC", "hgA", 50)
    assert commits == ["gitC", "gitB", "gitA"]


def test_candidate_commits_capped_newest_first(monkeypatch):
    pushes = {
        str(i): {"changesets": [{"node": f"hg{i}"}], "git_changesets": [f"git{i}"]}
        for i in range(1, 4)
    }
    monkeypatch.setattr(resolve, "_get_json", lambda url: {"pushes": pushes})
    commits = resolve._candidate_commits("autoland", "hg3", "hg0", 2)
    assert commits == ["git3", "git2"]


def test_candidate_commits_falls_back_to_head_commit(monkeypatch):
    def boom(url):
        raise resolve.requests.exceptions.RequestException("hg down")

    monkeypatch.setattr(resolve, "_get_json", boom)
    monkeypatch.setattr(resolve, "_hg_to_git", lambda rev: "gitHEAD")
    assert resolve._candidate_commits("autoland", "hgHEAD", "hgOLD", 50) == ["gitHEAD"]


def test_resolve_investigation_assembles_context(monkeypatch):
    task = {
        "tags": {"project": "autoland", "test-suite": "mochitest-browser-chrome"},
        "payload": {"env": {"GECKO_HEAD_REV": "hghead"}},
    }
    monkeypatch.setattr(resolve, "_get_json", lambda url: task)
    monkeypatch.setattr(
        resolve, "_failing_groups", lambda tid: [FailingGroup(GROUP, "a.js")]
    )
    monkeypatch.setattr(resolve, "_last_green", lambda *a: "greenrev")
    monkeypatch.setattr(resolve, "_candidate_commits", lambda *a: ["gitHead", "gitOld"])

    inv = resolve.resolve_investigation("TASK")
    assert inv.project == "autoland"
    assert inv.hg_revision == "hghead"
    assert inv.harness == "mochitest"
    assert inv.last_green_revision == "greenrev"
    assert inv.candidate_commits == ["gitHead", "gitOld"]
    assert inv.failure_commit == "gitHead"


def test_resolve_investigation_requires_revision(monkeypatch):
    monkeypatch.setattr(resolve, "_get_json", lambda url: {"tags": {}, "payload": {}})
    with pytest.raises(ValueError):
        resolve.resolve_investigation("TASK")
