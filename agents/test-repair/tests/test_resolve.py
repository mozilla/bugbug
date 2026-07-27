import pytest
from hackbot_agents.test_repair import resolve
from hackbot_agents.test_repair.resolve import FailingGroup
from mozci.errors import ParentPushNotFound

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
        last_green_revision=None,
        commit_range=resolve.CommitRange("head", None, 1, False),
    )
    return resolve.Investigation(**{**defaults, **kwargs})


class FakeResult:
    def __init__(self, group, ok):
        self.group = group
        self.ok = ok


class FakeTask:
    def __init__(self, platform, ok, failed_tests=()):
        self.platform = platform
        self.label = f"test-{platform}-mochitest-1"
        self.results = [FakeResult(GROUP, ok)]
        self.failure_types = {GROUP: [(t, "timeout") for t in failed_tests]}


class FakeSummary:
    def __init__(self, *tasks):
        self.tasks = list(tasks)


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


def _failing():
    return FailingGroup(GROUP, TESTS)


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


def test_platform_derived_flags():
    inv = _investigation(platform="linux1804-64-qr/debug")
    assert inv.debug_build is True
    assert inv.is_linux is True
    win = _investigation(platform="windows11-64-24h2/opt")
    assert win.debug_build is False
    assert win.is_linux is False


def test_last_green_returns_first_passing_ancestor(monkeypatch):
    green = FakePush("greenrev", {GROUP: FakeSummary(FakeTask(PLATFORM, ok=True))})
    head = FakePush("headrev", {}, parent=green)
    monkeypatch.setattr(resolve, "Push", lambda rev, branch=None: head)
    assert (
        resolve._last_green("autoland", "headrev", _failing(), PLATFORM) == "greenrev"
    )


def test_last_green_none_when_already_failing_upstream(monkeypatch):
    parent = FakePush(
        "parentrev",
        {GROUP: FakeSummary(FakeTask(PLATFORM, ok=False, failed_tests=TESTS))},
    )
    head = FakePush("headrev", {}, parent=parent)
    monkeypatch.setattr(resolve, "Push", lambda rev, branch=None: head)
    assert resolve._last_green("autoland", "headrev", _failing(), PLATFORM) is None


def test_last_green_skips_pushes_that_only_ran_elsewhere(monkeypatch):
    # The group passed on Windows but never ran on the failing Linux platform, so
    # it cannot anchor a last-green -- the walk must continue past it.
    green = FakePush("greenrev", {GROUP: FakeSummary(FakeTask(PLATFORM, ok=True))})
    other = FakePush(
        "otherrev",
        {GROUP: FakeSummary(FakeTask("windows11-64-24h2/opt", ok=True))},
        parent=green,
    )
    head = FakePush("headrev", {}, parent=other)
    monkeypatch.setattr(resolve, "Push", lambda rev, branch=None: head)
    assert (
        resolve._last_green("autoland", "headrev", _failing(), PLATFORM) == "greenrev"
    )


def test_test_status_ignores_failures_of_other_tests():
    # The manifest failed, but not for the test we are investigating.
    summary = FakeSummary(
        FakeTask(PLATFORM, ok=False, failed_tests=["dom/base/test/test_other.js"])
    )
    push = FakePush("rev", {GROUP: summary})
    assert resolve._test_status(push, GROUP, TESTS, PLATFORM) == "passed"
    assert resolve._test_status(push, GROUP, [], PLATFORM) == "failed"


def test_test_status_none_when_group_absent():
    assert resolve._test_status(FakePush("rev"), GROUP, TESTS, PLATFORM) is None


def test_last_green_fails_soft_on_error(monkeypatch):
    def boom(rev, branch=None):
        raise RuntimeError("mozci exploded")

    monkeypatch.setattr(resolve, "Push", boom)
    assert resolve._last_green("autoland", "headrev", _failing(), PLATFORM) is None


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
        resolve, "_failing_groups", lambda tid: [FailingGroup(GROUP, TESTS)]
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
    assert inv.platform == "linux1804-64-qr/debug"
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
