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
    # Every Firefox test task has kind=="test", so the suite must win over it.
    assert (
        resolve._harness({"kind": "test", "test-suite": "web-platform-tests"})
        == "web-platform-tests"
    )
    assert resolve._harness({}) == "unknown"


def test_debug_build_detection():
    assert resolve._is_debug_build({"test-platform": "linux1804-64-qr/debug"}) is True
    assert resolve._is_debug_build({"test-platform": "windows11-64/opt"}) is False
    assert resolve._is_debug_build({}) is False


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


def _hg2git(rev):
    return {"hgA": "gitA", "hgB": "gitB", "hgC": "gitC"}.get(rev)


def test_resolve_range_maps_only_the_endpoints(monkeypatch):
    pushes = {"2": {"changesets": [{"node": "hgB"}, {"node": "hgC"}]}}
    looked_up = []

    def fake_hg_to_git(rev):
        looked_up.append(rev)
        return _hg2git(rev)

    monkeypatch.setattr(resolve, "_get_json", lambda url: {"pushes": pushes})
    monkeypatch.setattr(resolve, "_hg_to_git", fake_hg_to_git)
    rng = resolve._resolve_range("autoland", "hgC", "hgA", 100)
    assert (rng.head, rng.base, rng.span, rng.complete) == ("gitC", "gitA", 2, True)
    # Two lando lookups regardless of range width; no per-commit mapping to fail.
    assert looked_up == ["hgC", "hgA"]


def test_resolve_range_without_last_green_is_incomplete(monkeypatch):
    pushes = {"1": {"changesets": [{"node": "hgA"}]}}
    monkeypatch.setattr(resolve, "_get_json", lambda url: {"pushes": pushes})
    monkeypatch.setattr(resolve, "_hg_to_git", _hg2git)
    rng = resolve._resolve_range("autoland", "hgA", None, 100)
    assert rng.head == "gitA"
    assert rng.base is None
    assert rng.complete is False


def test_resolve_range_capped_drops_the_base(monkeypatch):
    pushes = {str(i): {"changesets": [{"node": f"hg{i}"}]} for i in range(1, 6)}
    monkeypatch.setattr(resolve, "_get_json", lambda url: {"pushes": pushes})
    monkeypatch.setattr(resolve, "_hg_to_git", lambda rev: "gitC")
    rng = resolve._resolve_range("autoland", "hgC", "hgA", 2)
    # The base falls outside the capped clone, so it can no longer anchor it.
    assert rng.base is None
    assert rng.span == 2
    assert rng.complete is False


def test_resolve_range_none_when_head_unresolvable(monkeypatch):
    monkeypatch.setattr(resolve, "_hg_to_git", lambda rev: None)
    assert resolve._resolve_range("autoland", "hgHEAD", "hgOLD", 100) is None


def test_resolve_range_incomplete_when_base_unresolvable(monkeypatch):
    pushes = {"1": {"changesets": [{"node": "hgB"}]}}
    monkeypatch.setattr(resolve, "_get_json", lambda url: {"pushes": pushes})
    monkeypatch.setattr(
        resolve, "_hg_to_git", lambda rev: "gitB" if rev == "hgB" else None
    )
    rng = resolve._resolve_range("autoland", "hgB", "hgA", 100)
    assert rng.head == "gitB"
    assert rng.base is None
    assert rng.complete is False


def test_resolve_range_survives_pushlog_failure(monkeypatch):
    def boom(url):
        raise resolve.requests.exceptions.RequestException("hg down")

    monkeypatch.setattr(resolve, "_get_json", boom)
    monkeypatch.setattr(resolve, "_hg_to_git", lambda rev: "gitHEAD")
    rng = resolve._resolve_range("autoland", "hgHEAD", "hgOLD", 100)
    assert rng.head == "gitHEAD"
    assert rng.base is None
    assert rng.span == 1
    assert rng.complete is False


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
        resolve, "_failing_groups", lambda tid: [FailingGroup(GROUP, "a.js")]
    )
    monkeypatch.setattr(resolve, "_last_green", lambda *a: "greenrev")
    monkeypatch.setattr(
        resolve,
        "_resolve_range",
        lambda *a: resolve.CommitRange("gitHead", "gitBase", 2, True),
    )

    inv = resolve.resolve_investigation("TASK")
    assert inv.project == "autoland"
    assert inv.hg_revision == "hghead"
    assert inv.harness == "mochitest"
    assert inv.debug_build is True
    assert inv.last_green_revision == "greenrev"
    assert inv.failure_commit == "gitHead"
    assert inv.commit_range.base == "gitBase"
    assert inv.commit_range.complete is True
    assert inv.commit_range.span == 2


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
