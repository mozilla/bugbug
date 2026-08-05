import pytest
from hackbot_agents.test_repair import resolve
from hackbot_agents.test_repair.resolve import FailingGroup
from mozci.errors import ParentPushNotFound
from mozci.task import Status

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


class FakeLabelSummary:
    def __init__(self, status):
        self.status = status


class FakePush:
    def __init__(self, rev, summaries=None, parent=None, labels=None):
        self.rev = rev
        self.group_summaries = summaries or {}
        self.label_summaries = labels or {}
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


def test_label_status_maps_the_summary_status():
    label = "test-macosx1500-aarch64/opt-gtest-1proc"
    passed = FakePush("rev", labels={label: FakeLabelSummary(Status.PASS)})
    failed = FakePush("rev", labels={label: FakeLabelSummary(Status.FAIL)})
    flaky = FakePush("rev", labels={label: FakeLabelSummary(Status.INTERMITTENT)})
    assert resolve._label_status(passed, label) == "passed"
    assert resolve._label_status(failed, label) == "failed"
    # Both passed and failed here, so it cannot anchor a green.
    assert resolve._label_status(flaky, label) is None
    assert resolve._label_status(FakePush("rev"), label) is None


def test_last_green_label_walks_to_a_green_task(monkeypatch):
    # gtest reports no manifests, so the whole task is the finest granularity.
    label = "test-macosx1500-aarch64/opt-gtest-1proc"
    green = FakePush("greenrev", labels={label: FakeLabelSummary(Status.PASS)})
    # Did not run here at all: non-decisive, so the walk must continue.
    absent = FakePush("absentrev", labels={}, parent=green)
    head = FakePush("headrev", labels={}, parent=absent)
    monkeypatch.setattr(resolve, "Push", lambda rev, branch=None: head)
    assert resolve._last_green_label("autoland", "headrev", label) == "greenrev"


def test_last_green_label_none_when_already_failing_upstream(monkeypatch):
    label = "test-macosx1500-aarch64/opt-gtest-1proc"
    parent = FakePush("parentrev", labels={label: FakeLabelSummary(Status.FAIL)})
    head = FakePush("headrev", labels={}, parent=parent)
    monkeypatch.setattr(resolve, "Push", lambda rev, branch=None: head)
    assert resolve._last_green_label("autoland", "headrev", label) is None


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


def test_resolve_range_without_last_green_widens_to_ancestor_pushes(monkeypatch):
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
    rng = resolve._resolve_range("autoland", "hgA", None, 100)
    assert f"startID={500 - resolve.FALLBACK_RANGE_PUSHES}&endID=500" in urls[1]
    assert rng.head == "gitA"
    assert rng.base is None
    assert rng.span == 40
    # Wider, but the culprit is still not provably inside it.
    assert rng.complete is False


def test_resolve_range_fallback_window_is_capped(monkeypatch):
    pushes = {str(i): {"changesets": [{"node": f"hg{i}"}]} for i in range(1, 40)}
    monkeypatch.setattr(resolve, "_get_json", lambda url: {"pushes": pushes})
    monkeypatch.setattr(resolve, "_hg_to_git", _hg2git)
    rng = resolve._resolve_range("autoland", "hgA", None, 10)
    # The span drives the clone depth, so the cap has to hold here too.
    assert rng.span == 10
    assert rng.complete is False


def test_resolve_range_fallback_keeps_the_head_push_when_widening_fails(monkeypatch):
    def fake_get_json(url):
        if "changeset=hgA" in url:
            return {"pushes": {"500": {"changesets": [{"node": "hgA"}]}}}
        raise resolve.requests.exceptions.RequestException("hg down")

    monkeypatch.setattr(resolve, "_get_json", fake_get_json)
    monkeypatch.setattr(resolve, "_hg_to_git", _hg2git)
    rng = resolve._resolve_range("autoland", "hgA", None, 100)
    assert rng.head == "gitA"
    assert rng.span == 1
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
        resolve, "_last_green_label", lambda branch, rev, lbl: f"green-{lbl}"
    )
    monkeypatch.setattr(
        resolve,
        "_resolve_range",
        lambda *a: resolve.CommitRange("gitHead", "gitBase", 2, True),
    )

    inv = resolve.resolve_investigation("TASK")
    assert inv.group_based is False
    assert inv.failing_groups == []
    assert inv.label == label
    assert inv.last_green_revision == f"green-{label}"


def test_resolve_investigation_keeps_group_level_last_green_for_grouped_suites(
    monkeypatch,
):
    task = {
        "tags": {
            "project": "autoland",
            "test-suite": "mochitest-browser-chrome",
            "test-platform": PLATFORM,
            "label": f"test-{PLATFORM}-mochitest-browser-chrome-1",
        },
        "payload": {"env": {"GECKO_HEAD_REV": "hghead"}},
    }
    monkeypatch.setattr(resolve, "_get_json", lambda url: task)
    monkeypatch.setattr(
        resolve, "_failing_groups", lambda tid: [FailingGroup(GROUP, TESTS)]
    )
    monkeypatch.setattr(resolve, "_last_green", lambda *a: "greenrev")

    def no_label_lookup(*a):
        raise AssertionError("label-level last-green is only for group-less suites")

    monkeypatch.setattr(resolve, "_last_green_label", no_label_lookup)
    monkeypatch.setattr(
        resolve,
        "_resolve_range",
        lambda *a: resolve.CommitRange("gitHead", "gitBase", 2, True),
    )

    inv = resolve.resolve_investigation("TASK")
    assert inv.group_based is True
    assert inv.last_green_revision == "greenrev"


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
