"""Tests for the test-failure regression gate (is_new_test_failure)."""

from app import regression
from mozci.errors import ParentPushNotFound
from mozci.task import Status

GROUP = "dom/base/test/mochitest.ini"


class FakeSummary:
    def __init__(self, status, running=False):
        self.status = status
        self.running = running


class FakePush:
    def __init__(self, rev, summaries=None, parent=None):
        self.rev = rev
        self._summaries = summaries or {}
        self._parent = parent

    @property
    def group_summaries(self):
        return self._summaries

    @property
    def parent(self):
        if self._parent is None:
            raise ParentPushNotFound(f"no parent for {self.rev}")
        return self._parent

    def is_group_running(self, summary):
        return getattr(summary, "running", False)


def _install_head(monkeypatch, head):
    monkeypatch.setattr(regression, "Push", lambda rev, branch=None: head)


def test_new_failure_when_ancestor_passed(monkeypatch):
    parent = FakePush("parentrev", {GROUP: FakeSummary(Status.PASS)})
    head = FakePush("headrev", {}, parent=parent)
    _install_head(monkeypatch, head)
    is_new, last_green = regression.is_new_test_failure("autoland", "headrev", GROUP)
    assert is_new is True
    assert last_green == "parentrev"


def test_inherited_when_ancestor_failed(monkeypatch):
    parent = FakePush("parentrev", {GROUP: FakeSummary(Status.FAIL)})
    head = FakePush("headrev", {}, parent=parent)
    _install_head(monkeypatch, head)
    is_new, last_green = regression.is_new_test_failure("autoland", "headrev", GROUP)
    assert is_new is False
    assert last_green is None


def test_intermittent_ancestor_is_skipped_until_decisive(monkeypatch):
    green = FakePush("greenrev", {GROUP: FakeSummary(Status.PASS)})
    flaky = FakePush(
        "flakyrev", {GROUP: FakeSummary(Status.INTERMITTENT)}, parent=green
    )
    head = FakePush("headrev", {}, parent=flaky)
    _install_head(monkeypatch, head)
    is_new, last_green = regression.is_new_test_failure("autoland", "headrev", GROUP)
    assert is_new is True
    assert last_green == "greenrev"


def test_coalesced_ancestor_without_group_is_skipped(monkeypatch):
    green = FakePush("greenrev", {GROUP: FakeSummary(Status.PASS)})
    coalesced = FakePush("coalrev", {}, parent=green)  # group never ran here
    head = FakePush("headrev", {}, parent=coalesced)
    _install_head(monkeypatch, head)
    is_new, last_green = regression.is_new_test_failure("autoland", "headrev", GROUP)
    assert is_new is True
    assert last_green == "greenrev"


def test_running_ancestor_is_pending(monkeypatch):
    parent = FakePush("parentrev", {GROUP: FakeSummary(Status.FAIL, running=True)})
    head = FakePush("headrev", {}, parent=parent)
    _install_head(monkeypatch, head)
    state, _ = regression._classify(
        "autoland",
        "headrev",
        lambda push: regression._group_status(push, GROUP),
        f"group {GROUP}",
    )
    assert state is regression._PENDING


def test_no_ancestor_ran_group_fails_open(monkeypatch):
    parent = FakePush("parentrev", {})  # group not present, no further parent
    head = FakePush("headrev", {}, parent=parent)
    _install_head(monkeypatch, head)
    is_new, last_green = regression.is_new_test_failure("autoland", "headrev", GROUP)
    assert is_new is True
    assert last_green is None


def test_mozci_error_fails_open(monkeypatch):
    def boom(rev, branch=None):
        raise RuntimeError("mozci exploded")

    monkeypatch.setattr(regression, "Push", boom)
    is_new, last_green = regression.is_new_test_failure("autoland", "headrev", GROUP)
    assert is_new is True
    assert last_green is None


def test_pending_past_deadline_fails_open(monkeypatch):
    parent = FakePush("parentrev", {GROUP: FakeSummary(Status.FAIL, running=True)})
    head = FakePush("headrev", {}, parent=parent)
    _install_head(monkeypatch, head)
    # Force the poll loop to time out immediately without real sleeping.
    monkeypatch.setattr(regression.time, "sleep", lambda s: None)
    ticks = iter([0.0, regression.MAX_WAIT_SECONDS + 1])
    monkeypatch.setattr(regression.time, "monotonic", lambda: next(ticks))
    is_new, last_green = regression.is_new_test_failure("autoland", "headrev", GROUP)
    assert is_new is True
    assert last_green is None
