"""Tests for the test-failure regression gate (new_test_failures)."""

from unittest.mock import patch

import pytest
from app import regression
from mozci.errors import ParentPushNotFound

GROUP = "dom/base/test/mochitest.ini"
OTHER_GROUP = "layout/test/mochitest.ini"
CONFIG = ("linux1804-64", "opt")
OTHER_CONFIG = ("windows11-64", "debug")


def _job(task_id="T1", result="testfailed", state="completed"):
    return {"result": result, "state": state, "task_id": task_id}


class FakePush:
    """A push whose only job is to yield the ancestor chain, as mozci does."""

    def __init__(self, rev, parent=None):
        self.rev = rev
        self._parent = parent

    @property
    def parent(self):
        if self._parent is None:
            raise ParentPushNotFound("no parent", rev=self.rev, branch="autoland")
        return self._parent


def _chain(depth: int):
    push = None
    for i in range(depth, 0, -1):
        push = FakePush(f"rev{i}", parent=push)
    return FakePush("head", parent=push)


def _run(groups, jobs, results, *, depth=1, poll=None):
    """jobs: {rev: {config: [job]}}; results: {rev: {task_id: {group: passed}}}."""
    snapshots = [(jobs, results)] + list(poll or [])
    state = {"attempt": 0}

    def snapshot():
        return snapshots[min(state["attempt"], len(snapshots) - 1)]

    with (
        patch.object(regression, "Push", return_value=_chain(depth)),
        patch.object(
            regression.treeherder,
            "config_jobs",
            lambda p, rev, plat, opt: snapshot()[0].get(rev, {}).get((plat, opt), []),
        ),
        patch.object(
            regression.treeherder,
            "group_results",
            lambda p, rev: snapshot()[1].get(rev, {}),
        ),
        patch.object(
            regression.time,
            "sleep",
            lambda s: state.update(attempt=state["attempt"] + 1),
        ),
    ):
        return regression.new_test_failures("autoland", "head", CONFIG, list(groups))


def test_new_failure_when_ancestor_passed():
    assert _run(
        [GROUP],
        {"rev1": {CONFIG: [_job()]}},
        {"rev1": {"T1": {GROUP: True}}},
    ) == {GROUP}


def test_inherited_when_ancestor_failed():
    assert (
        _run([GROUP], {"rev1": {CONFIG: [_job()]}}, {"rev1": {"T1": {GROUP: False}}})
        == set()
    )


def test_retriggered_green_ancestor_counts_as_passed():
    jobs = {"rev1": {CONFIG: [_job(task_id="T1"), _job(task_id="T2")]}}
    results = {"rev1": {"T1": {GROUP: False}, "T2": {GROUP: True}}}
    assert _run([GROUP], jobs, results) == {GROUP}


def test_other_configuration_failure_does_not_mask_new_failure():
    # The manifest is already broken on another platform at the parent push; only
    # this task's own label may decide.
    jobs = {
        "rev1": {OTHER_CONFIG: [_job(task_id="OTHER")]},
        "rev2": {CONFIG: [_job(task_id="T2")]},
    }
    results = {
        "rev1": {"OTHER": {GROUP: False}},
        "rev2": {"T2": {GROUP: True}},
    }
    assert _run([GROUP], jobs, results, depth=2) == {GROUP}


def test_unfinished_ancestor_is_waited_then_inherited():
    jobs = {"rev1": {CONFIG: [_job(result=None, state="running")]}}
    poll = [({"rev1": {CONFIG: [_job()]}}, {"rev1": {"T1": {GROUP: False}}})]
    assert _run([GROUP], jobs, {}, poll=poll) == set()


def test_unsettled_sibling_defers_a_failed_group():
    # Same precedence as the build path: a run that may still turn green outranks
    # an existing failure, so wait instead of calling it inherited.
    jobs = {"rev1": {CONFIG: [_job(task_id="T1"), _job(task_id="T2", state="running")]}}
    results = {"rev1": {"T1": {GROUP: False}}}
    poll = [
        (
            {"rev1": {CONFIG: [_job(task_id="T1"), _job(task_id="T2")]}},
            {"rev1": {"T1": {GROUP: False}, "T2": {GROUP: True}}},
        )
    ]
    assert _run([GROUP], jobs, results, poll=poll) == {GROUP}


def test_ancestor_that_never_ran_the_group_is_skipped():
    # Coalesced, or the manifest was chunked into another task: non-decisive.
    jobs = {
        "rev1": {CONFIG: [_job(task_id="T1")]},
        "rev2": {CONFIG: [_job(task_id="T2")]},
    }
    results = {
        "rev1": {"T1": {OTHER_GROUP: False}},
        "rev2": {"T2": {GROUP: True}},
    }
    assert _run([GROUP], jobs, results, depth=2) == {GROUP}


def test_groups_are_judged_independently():
    jobs = {"rev1": {CONFIG: [_job()]}}
    results = {"rev1": {"T1": {GROUP: True, OTHER_GROUP: False}}}
    assert _run([GROUP, OTHER_GROUP], jobs, results) == {GROUP}


def test_no_ancestor_ran_the_group_fails_open():
    assert _run([GROUP], {}, {}) == {GROUP}


def test_lookup_error_fails_open():
    def explode(*args, **kwargs):
        raise RuntimeError("treeherder down")

    with (
        patch.object(regression, "Push", return_value=_chain(1)),
        patch.object(regression.treeherder, "config_jobs", explode),
        patch.object(regression.time, "sleep"),
    ):
        assert regression.new_test_failures(
            "autoland", "head", CONFIG, [GROUP, OTHER_GROUP]
        ) == {GROUP, OTHER_GROUP}


def test_groups_of_one_task_share_one_wait_budget():
    # The budget is per call, not per group: two pending groups must not each get
    # their own MAX_WAIT_SECONDS.
    jobs = {"rev1": {CONFIG: [_job(result=None, state="running")]}}

    def no_sleep(_seconds):
        pytest.fail("waited past a spent deadline")

    with (
        patch.object(regression, "Push", return_value=_chain(1)),
        patch.object(
            regression.treeherder,
            "config_jobs",
            lambda p, rev, plat, opt: jobs.get(rev, {}).get((plat, opt), []),
        ),
        patch.object(regression.treeherder, "group_results", lambda p, rev: {}),
        patch.object(regression.time, "sleep", no_sleep),
        patch.object(
            regression.time,
            "monotonic",
            side_effect=[0.0, regression.MAX_WAIT_SECONDS + 1],
        ),
    ):
        assert regression.new_test_failures(
            "autoland", "head", CONFIG, [GROUP, OTHER_GROUP]
        ) == {GROUP, OTHER_GROUP}


def test_missing_configuration_reports_every_group_as_new():
    # Without a configuration there is nothing to compare against, so nothing may
    # be silently dropped.
    assert regression.new_test_failures("autoland", "head", (None, None), [GROUP]) == {
        GROUP
    }
