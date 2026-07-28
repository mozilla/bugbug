"""Tests for the test-failure regression gate (new_test_failures)."""

from types import SimpleNamespace

import pytest
from app import regression
from mozci.errors import ParentPushNotFound

GROUP = "dom/base/test/mochitest.ini"
OTHER_GROUP = "layout/test/mochitest.ini"
LABEL = "test-linux1804-64/opt-mochitest-browser-chrome-1"
OTHER_LABEL = "test-windows11-64/debug-mochitest-browser-chrome-1"


def _task(label=LABEL, groups=(), state="completed", result="failed"):
    """A test task reporting (group, ok) pairs."""
    return SimpleNamespace(
        label=label,
        state=state,
        result=result,
        failed=result == "failed",
        results=[SimpleNamespace(group=g, ok=ok, duration=1) for g, ok in groups],
    )


class FakePush:
    def __init__(self, rev, tasks=(), parent=None, scheduled=()):
        self.rev = rev
        self.tasks = list(tasks)
        self.scheduled_task_labels = set(scheduled)
        self._parent = parent

    @property
    def parent(self):
        if self._parent is None:
            raise ParentPushNotFound(f"no parent for {self.rev}")
        return self._parent


def _install_head(monkeypatch, head):
    monkeypatch.setattr(regression, "Push", lambda rev, branch=None: head)


def _check(groups=(GROUP,)):
    return regression.new_test_failures("autoland", "headrev", LABEL, list(groups))


def _group_state(monkeypatch, head, group=GROUP):
    _install_head(monkeypatch, head)
    return regression._classify(
        head,
        lambda push: regression._group_status(push, group, LABEL),
        f"group {group}",
    )


def test_new_failure_when_ancestor_passed(monkeypatch):
    parent = FakePush("parentrev", [_task(groups=[(GROUP, True)], result="passed")])
    _install_head(monkeypatch, FakePush("headrev", parent=parent))
    assert _check() == {GROUP}


def test_inherited_when_ancestor_failed(monkeypatch):
    parent = FakePush("parentrev", [_task(groups=[(GROUP, False)])])
    _install_head(monkeypatch, FakePush("headrev", parent=parent))
    assert _check() == set()


def test_retriggered_green_ancestor_counts_as_passed(monkeypatch):
    # Any green run wins, so the failure here is treated as new (errs toward
    # running the agent rather than dropping a regression).
    parent = FakePush(
        "parentrev", [_task(groups=[(GROUP, False)]), _task(groups=[(GROUP, True)])]
    )
    _install_head(monkeypatch, FakePush("headrev", parent=parent))
    assert _check() == {GROUP}


def test_other_configuration_failure_does_not_mask_new_failure(monkeypatch):
    # The manifest is already broken on another platform at the parent push. The
    # all-configuration group summary would call that an inherited failure; only
    # this task's own label may decide.
    green = FakePush("greenrev", [_task(groups=[(GROUP, True)], result="passed")])
    parent = FakePush(
        "parentrev", [_task(label=OTHER_LABEL, groups=[(GROUP, False)])], parent=green
    )
    _install_head(monkeypatch, FakePush("headrev", parent=parent))
    assert _check() == {GROUP}


def test_unfinished_ancestor_task_is_pending(monkeypatch):
    # The parent's equivalent task is still running, so it has published no
    # results yet. It must be waited for, not skipped as non-decisive.
    parent = FakePush("parentrev", [_task(state="running", result=None)])
    head = FakePush("headrev", parent=parent)
    assert _group_state(monkeypatch, head) is regression._PENDING


def test_unsettled_sibling_defers_a_failed_group(monkeypatch):
    # Same precedence as the build path: a retrigger that may still turn green
    # outranks an existing failure, so wait instead of calling it inherited.
    parent = FakePush(
        "parentrev",
        [_task(groups=[(GROUP, False)]), _task(state="running", result=None)],
    )
    head = FakePush("headrev", parent=parent)
    assert _group_state(monkeypatch, head) is regression._PENDING


def test_scheduled_but_unreported_ancestor_task_is_pending(monkeypatch):
    # The label was scheduled on the parent but no task is visible yet.
    parent = FakePush("parentrev", [], scheduled=[LABEL])
    head = FakePush("headrev", parent=parent)
    assert _group_state(monkeypatch, head) is regression._PENDING


def test_coalesced_ancestor_is_skipped(monkeypatch):
    # Nothing ran and nothing was scheduled: non-decisive, keep walking.
    green = FakePush("greenrev", [_task(groups=[(GROUP, True)], result="passed")])
    coalesced = FakePush("coalrev", [], parent=green)
    _install_head(monkeypatch, FakePush("headrev", parent=coalesced))
    assert _check() == {GROUP}


def test_ancestor_ran_label_without_the_group_is_skipped(monkeypatch):
    # The manifest was chunked into a different task here: non-decisive.
    green = FakePush("greenrev", [_task(groups=[(GROUP, True)], result="passed")])
    other = FakePush("otherrev", [_task(groups=[(OTHER_GROUP, False)])], parent=green)
    _install_head(monkeypatch, FakePush("headrev", parent=other))
    assert _check() == {GROUP}


def test_groups_are_judged_independently(monkeypatch):
    parent = FakePush(
        "parentrev",
        [_task(groups=[(GROUP, True), (OTHER_GROUP, False)], result="passed")],
    )
    _install_head(monkeypatch, FakePush("headrev", parent=parent))
    assert _check([GROUP, OTHER_GROUP]) == {GROUP}


def test_one_ancestor_walk_serves_every_group(monkeypatch):
    # mozci memoizes a push's task list per instance, so all groups of a task must
    # share one head push rather than refetching the ancestors for each.
    parent = FakePush("parentrev", [_task(groups=[(GROUP, True)], result="passed")])
    head = FakePush("headrev", parent=parent)
    builds = []

    def build(rev, branch=None):
        builds.append(rev)
        return head

    monkeypatch.setattr(regression, "Push", build)
    regression.new_test_failures(
        "autoland", "headrev", LABEL, [GROUP, OTHER_GROUP, "c"]
    )
    assert builds == ["headrev"]


def test_no_ancestor_ran_group_fails_open(monkeypatch):
    _install_head(monkeypatch, FakePush("headrev", parent=FakePush("parentrev")))
    assert _check() == {GROUP}


def test_mozci_error_fails_open(monkeypatch):
    def boom(rev, branch=None):
        raise RuntimeError("mozci exploded")

    monkeypatch.setattr(regression, "Push", boom)
    assert _check([GROUP, OTHER_GROUP]) == {GROUP, OTHER_GROUP}


def test_pending_past_deadline_fails_open(monkeypatch):
    parent = FakePush("parentrev", [_task(state="running", result=None)])
    _install_head(monkeypatch, FakePush("headrev", parent=parent))
    monkeypatch.setattr(regression.time, "sleep", lambda s: None)
    ticks = iter([0.0, regression.MAX_WAIT_SECONDS + 1])
    monkeypatch.setattr(regression.time, "monotonic", lambda: next(ticks))
    assert _check() == {GROUP}


def test_groups_of_one_task_share_one_wait_budget(monkeypatch):
    # The budget is per call, not per group: two pending groups must not each get
    # their own MAX_WAIT_SECONDS.
    parent = FakePush("parentrev", [_task(state="running", result=None)])
    _install_head(monkeypatch, FakePush("headrev", parent=parent))

    def no_sleep(_seconds):
        pytest.fail("waited past a spent deadline")

    monkeypatch.setattr(regression.time, "sleep", no_sleep)
    ticks = iter([0.0, regression.MAX_WAIT_SECONDS + 1])
    monkeypatch.setattr(regression.time, "monotonic", lambda: next(ticks))
    assert _check([GROUP, OTHER_GROUP]) == {GROUP, OTHER_GROUP}
