import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app import consumer

FIXTURES = Path(__file__).parent / "fixtures"

DECISION_TASK_ID = "DECISION"


def setup_function():
    consumer._seen.clear()
    consumer._seen_tests.clear()
    consumer._seen_groups.clear()
    consumer._test_run_times.clear()


@pytest.fixture(autouse=True)
def fresh_push():
    """Every test but the staleness one runs against a push that just landed.

    Also keeps the real push-age gate, which queries hgmo, off the network.
    """
    with patch.object(consumer.regression, "is_stale_push", return_value=False) as gate:
        yield gate


def _sample_bodies():
    data = json.loads((FIXTURES / "pulse_messages.json").read_text())
    # The inspector wraps the real AMQP body under "payload".
    return [m["payload"] for m in data]


def _task_def(revision="hgrev", parent=DECISION_TASK_ID):
    """A task definition as fetched from Taskcluster.

    ``parent`` defaults to the decision task, i.e. scheduled by the push itself.
    """
    return {
        "taskGroupId": DECISION_TASK_ID,
        "extra": {"parent": parent},
        "payload": {"env": {"GECKO_HEAD_REV": revision}},
    }


def _build_msg(task_id="ABC", project="autoland", label="build-linux64/opt"):
    return {
        "status": {"taskId": task_id},
        "runId": 0,
        "task": {
            "tags": {
                "kind": "build",
                "project": project,
                "label": label,
                "createdForUser": "dev@mozilla.com",
            }
        },
    }


def _test_msg(
    task_id="TT",
    project="autoland",
    kind="mochitest",
    label="test-linux1804-64/opt-mochitest-browser-chrome-1",
    suite="mochitest-browser-chrome",
    group_id="G1",
):
    return {
        "status": {"taskId": task_id, "taskGroupId": group_id},
        "runId": 0,
        "task": {
            "tags": {
                "kind": kind,
                "project": project,
                "label": label,
                "test-suite": suite,
                "createdForUser": "dev@mozilla.com",
            }
        },
    }


def test_sample_messages_route_to_test_repair_not_build():
    # The captured samples are all test tasks. They now reach the test-repair path
    # (they were ignored outright when the listener only handled builds), and none of
    # them triggers the build-repair agent.
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_task", return_value=_task_def()),
        patch.object(consumer.treeherder, "failing_groups", return_value=[]) as groups,
        patch.object(consumer.treeherder, "job_for_task", return_value=None),
        patch.object(consumer.treeherder, "await_skip_reason", return_value=None),
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        for body in _sample_bodies():
            assert consumer.process(body, executor) is None
    # At least the autoland samples were routed to the test-repair path.
    assert groups.called
    trigger.assert_not_called()
    executor.submit.assert_not_called()


def test_missing_label_is_skipped_not_crashed():
    executor = MagicMock()
    body = {"status": {"taskId": "XYZ"}, "task": {"tags": {"project": "autoland"}}}
    with patch.object(consumer.client, "trigger_run") as trigger:
        assert consumer.process(body, executor) is None
    trigger.assert_not_called()


def test_build_failure_triggers_run_and_submits_poll():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_task", return_value=_task_def()),
        patch.object(consumer.lando, "hg_to_git", return_value="deadbeef"),
        patch.object(consumer.treeherder, "recheck_skip_reason", return_value=None),
        patch.object(consumer.regression, "is_new_build_failure", return_value=True),
        patch.object(consumer.client, "trigger_run", return_value="run-1") as trigger,
    ):
        run_id = consumer.process(_build_msg(), executor)

    assert run_id == "run-1"
    trigger.assert_called_once()
    inputs = trigger.call_args.args[0]
    assert inputs["failure_tasks"] == {"build-linux64/opt": "ABC"}
    assert "git_commits" not in inputs
    executor.submit.assert_called_once()
    fn, ctx = executor.submit.call_args.args
    assert fn is consumer.worker.poll_and_notify
    assert ctx.run_id == "run-1"
    assert ctx.git_commit == "deadbeef"
    assert ctx.hg_revision == "hgrev"
    assert ctx.task_id == "ABC"
    assert ctx.repo == "autoland"
    assert ctx.developer_email == "dev@mozilla.com"


def test_only_failure_tasks_sent_to_agent():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_task", return_value=_task_def()),
        patch.object(consumer.lando, "hg_to_git", return_value="deadbeef"),
        patch.object(consumer.treeherder, "recheck_skip_reason", return_value=None),
        patch.object(consumer.regression, "is_new_build_failure", return_value=True),
        patch.object(consumer.client, "trigger_run", return_value="run-1") as trigger,
    ):
        consumer.process(_build_msg(), executor)

    # The agent resolves the push (and its authors) itself; the listener
    # only hands it the failing tasks.
    inputs = trigger.call_args.args[0]
    assert inputs["failure_tasks"] == {"build-linux64/opt": "ABC"}
    assert "git_commit" not in inputs


def test_same_revision_triggers_once():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_task", return_value=_task_def()),
        patch.object(consumer.lando, "hg_to_git", return_value="deadbeef"),
        patch.object(consumer.treeherder, "recheck_skip_reason", return_value=None),
        patch.object(consumer.regression, "is_new_build_failure", return_value=True),
        patch.object(consumer.client, "trigger_run", return_value="run-1") as trigger,
    ):
        consumer.process(_build_msg(task_id="T1"), executor)
        consumer.process(_build_msg(task_id="T2"), executor)

    trigger.assert_called_once()


def test_inherited_failure_is_skipped_before_mapping():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_task", return_value=_task_def()),
        patch.object(consumer.treeherder, "recheck_skip_reason", return_value=None),
        patch.object(consumer.regression, "is_new_build_failure", return_value=False),
        patch.object(consumer.lando, "hg_to_git") as hg_to_git,
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        assert consumer.process(_build_msg(), executor) is None

    hg_to_git.assert_not_called()
    trigger.assert_not_called()
    executor.submit.assert_not_called()


def test_multiple_builds_same_revision_trigger_once():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_task", return_value=_task_def()),
        patch.object(consumer.lando, "hg_to_git", return_value="deadbeef"),
        patch.object(consumer.treeherder, "recheck_skip_reason", return_value=None),
        patch.object(consumer.regression, "is_new_build_failure", return_value=True),
        patch.object(consumer.client, "trigger_run", return_value="run-1") as trigger,
    ):
        consumer.process(_build_msg(task_id="T1", label="build-linux64/opt"), executor)
        consumer.process(_build_msg(task_id="T2", label="build-macosx64/opt"), executor)

    trigger.assert_called_once()


def test_inherited_label_does_not_suppress_new_label_on_same_revision():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_task", return_value=_task_def()),
        patch.object(consumer.lando, "hg_to_git", return_value="deadbeef"),
        patch.object(
            consumer.regression, "is_new_build_failure", side_effect=[False, True]
        ),
        patch.object(consumer.client, "trigger_run", return_value="run-1") as trigger,
    ):
        # Inherited failure on the first label must not mark the revision seen.
        assert (
            consumer.process(
                _build_msg(task_id="T1", label="build-linux64/opt"), executor
            )
            is None
        )
        # A genuine regression on another label of the same push still runs.
        assert (
            consumer.process(
                _build_msg(task_id="T2", label="build-macosx64/opt"), executor
            )
            == "run-1"
        )

    trigger.assert_called_once()


def test_unwatched_project_skipped_before_api_call():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_task") as get_task,
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        assert consumer.process(_build_msg(project="try"), executor) is None

    get_task.assert_not_called()
    trigger.assert_not_called()


def test_backfilled_task_skipped_before_push_checks(fresh_push):
    executor = MagicMock()
    backfill = _task_def(parent="ACTION-CALLBACK")
    with (
        patch.object(consumer.taskcluster, "get_task", return_value=backfill),
        patch.object(consumer.treeherder, "recheck_skip_reason", return_value=None),
        patch.object(consumer.regression, "is_new_build_failure") as is_new,
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        assert consumer.process(_build_msg(), executor) is None

    fresh_push.assert_not_called()
    is_new.assert_not_called()
    trigger.assert_not_called()
    executor.submit.assert_not_called()


def test_stale_push_skipped_before_regression_check(fresh_push):
    executor = MagicMock()
    fresh_push.return_value = True
    with (
        patch.object(consumer.taskcluster, "get_task", return_value=_task_def()),
        patch.object(consumer.treeherder, "recheck_skip_reason", return_value=None),
        patch.object(consumer.regression, "is_new_build_failure") as is_new,
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        assert consumer.process(_build_msg(), executor) is None

    fresh_push.assert_called_once_with(
        "autoland", "hgrev", consumer.settings.max_push_age_hours * 3600
    )
    # The regression check can block for an hour, so it must come after.
    is_new.assert_not_called()
    trigger.assert_not_called()
    executor.submit.assert_not_called()


def test_stale_push_is_not_marked_seen():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_task", return_value=_task_def()),
        patch.object(consumer.regression, "is_stale_push", side_effect=[True, False]),
        patch.object(consumer.lando, "hg_to_git", return_value="deadbeef"),
        patch.object(consumer.treeherder, "recheck_skip_reason", return_value=None),
        patch.object(consumer.regression, "is_new_build_failure", return_value=True),
        patch.object(consumer.client, "trigger_run", return_value="run-1"),
    ):
        assert consumer.process(_build_msg(task_id="T1"), executor) is None
        # A stale verdict is not a claim on the revision, so a later message for
        # it (e.g. once the push date becomes readable) is still handled.
        assert consumer.process(_build_msg(task_id="T2"), executor) == "run-1"


def test_unmappable_revision_skipped():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_task", return_value=_task_def()),
        patch.object(consumer.treeherder, "recheck_skip_reason", return_value=None),
        patch.object(consumer.regression, "is_new_build_failure", return_value=True),
        patch.object(consumer.lando, "hg_to_git", return_value=None),
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        assert consumer.process(_build_msg(), executor) is None

    trigger.assert_not_called()
    executor.submit.assert_not_called()


def test_trigger_failure_releases_revision_for_retry():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_task", return_value=_task_def()),
        patch.object(consumer.lando, "hg_to_git", return_value="deadbeef"),
        patch.object(consumer.treeherder, "recheck_skip_reason", return_value=None),
        patch.object(consumer.regression, "is_new_build_failure", return_value=True),
        patch.object(
            consumer.client, "trigger_run", side_effect=[RuntimeError("boom"), "run-2"]
        ) as trigger,
    ):
        assert consumer.process(_build_msg(task_id="T1"), executor) is None
        # Same revision can be retried because the failed claim was released.
        assert consumer.process(_build_msg(task_id="T2"), executor) == "run-2"

    assert trigger.call_count == 2


_GROUP = "dom/base/test/mochitest.ini"


@pytest.fixture
def env(monkeypatch):
    """The test-repair path's external seams, as named mocks.

    Defaults describe the happy path: one failing group, not intermittent, a new
    regression, revision mappable, trigger succeeds.
    """
    mocks = SimpleNamespace(
        get_task=MagicMock(return_value=_task_def()),
        failing_groups=MagicMock(return_value=[_GROUP]),
        job_for_task=MagicMock(
            return_value={
                "failure_classification_id": 6,
                "platform": "linux1804-64",
                "platform_option": "opt",
            }
        ),
        recheck_skip_reason=MagicMock(return_value=None),
        await_skip_reason=MagicMock(return_value=None),
        intermittent_match=MagicMock(
            return_value=consumer.treeherder.IntermittentMatch()
        ),
        failing_tests=MagicMock(return_value=["dom/base/test/test_a.html"]),
        has_clean_history=MagicMock(return_value=False),
        new_test_failures=MagicMock(
            side_effect=lambda p, r, cfg, groups, abort=None: set(groups)
        ),
        is_new_task_failure=MagicMock(return_value=True),
        hg_to_git=MagicMock(return_value="gitH"),
        trigger_run=MagicMock(return_value="tr-1"),
        executor=MagicMock(),
    )
    monkeypatch.setattr(consumer.taskcluster, "get_task", mocks.get_task)
    monkeypatch.setattr(consumer.treeherder, "failing_groups", mocks.failing_groups)
    monkeypatch.setattr(consumer.treeherder, "job_for_task", mocks.job_for_task)
    monkeypatch.setattr(
        consumer.treeherder, "recheck_skip_reason", mocks.recheck_skip_reason
    )
    monkeypatch.setattr(
        consumer.treeherder, "await_skip_reason", mocks.await_skip_reason
    )
    monkeypatch.setattr(
        consumer.treeherder, "intermittent_match", mocks.intermittent_match
    )
    monkeypatch.setattr(consumer.treeherder, "failing_tests", mocks.failing_tests)
    monkeypatch.setattr(
        consumer.flakiness, "has_clean_history", mocks.has_clean_history
    )
    monkeypatch.setattr(
        consumer.regression, "new_test_failures", mocks.new_test_failures
    )
    monkeypatch.setattr(
        consumer.regression, "is_new_task_failure", mocks.is_new_task_failure
    )
    monkeypatch.setattr(consumer.lando, "hg_to_git", mocks.hg_to_git)
    monkeypatch.setattr(consumer.client, "trigger_run", mocks.trigger_run)
    return mocks


def test_test_failure_triggers_rca_run(env):
    run_id = consumer.process(_test_msg(), env.executor)

    assert run_id == "tr-1"
    env.trigger_run.assert_called_once()
    inputs = env.trigger_run.call_args.args[0]
    assert env.trigger_run.call_args.kwargs["agent_name"] == "test-repair"
    # The agent resolves the test, commit range and clone depth itself; the
    # listener only hands it the failing task.
    assert inputs["failure_tasks"] == {
        "test-linux1804-64/opt-mochitest-browser-chrome-1": "TT"
    }
    assert "test_id" not in inputs
    assert "candidate_commits" not in inputs
    fn, ctx = env.executor.submit.call_args.args
    assert fn is consumer.worker.poll_and_notify
    assert ctx.agent == "test-repair"
    assert ctx.test_groups == [_GROUP]


def test_treeherder_intermittent_skipped_before_any_walk(env):
    # Treeherder's own verdict rules the failure out before any mozci work.
    env.await_skip_reason.return_value = "intermittent"
    assert consumer.process(_test_msg(), env.executor) is None
    env.failing_groups.assert_not_called()
    env.new_test_failures.assert_not_called()
    env.trigger_run.assert_not_called()


def test_unclassified_failure_is_investigated(env):
    # "not classified" / "new failure" leave the decision to the mozci walk.
    env.await_skip_reason.return_value = None
    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    env.new_test_failures.assert_called_once()


def test_inherited_test_group_skipped(env):
    env.new_test_failures.side_effect = lambda *_: set()
    assert consumer.process(_test_msg(), env.executor) is None
    env.trigger_run.assert_not_called()


def test_one_run_per_push(env):
    # The agent reads the push's other failures itself, so the first task worth
    # investigating is enough; later failing tasks of the same push are skipped.
    assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    assert consumer.process(_test_msg(task_id="B"), env.executor) is None
    env.trigger_run.assert_called_once()


def test_later_task_of_a_claimed_push_stops_before_treeherder(env):
    assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    env.job_for_task.reset_mock()
    env.failing_groups.reset_mock()
    assert consumer.process(_test_msg(task_id="B"), env.executor) is None
    env.job_for_task.assert_not_called()
    env.failing_groups.assert_not_called()


def test_no_failing_groups_skips(env):
    env.failing_groups.return_value = []
    assert consumer.process(_test_msg(), env.executor) is None
    env.trigger_run.assert_not_called()


def test_task_without_group_results_still_triggers_run(env):
    # A task-level failure (crash, timeout) records no per-manifest results; that
    # must not be mistaken for "nothing failed" and drop a real regression.
    env.failing_groups.side_effect = consumer.treeherder.GroupResultsUnavailable(
        "no group results for task ERR"
    )
    assert consumer.process(_test_msg(task_id="ERR"), env.executor) == "tr-1"
    env.trigger_run.assert_called_once()
    # With no groups resolved there is nothing to filter or to name.
    env.new_test_failures.assert_not_called()
    _, ctx = env.executor.submit.call_args.args
    assert ctx.test_groups == []


def test_unreadable_group_results_triggers_once_per_push(env):
    env.failing_groups.side_effect = RuntimeError("treeherder down")
    consumer.process(_test_msg(task_id="A"), env.executor)
    consumer.process(_test_msg(task_id="B"), env.executor)
    env.trigger_run.assert_called_once()


def test_rejected_task_does_not_suppress_a_real_regression_on_the_push(env):
    # The push is claimed only when a run is triggered, so a task rejected as
    # inherited leaves the push open for the next failing task -- which may be the
    # genuine regression.
    env.new_test_failures.side_effect = [set(), {_GROUP}]
    assert consumer.process(_test_msg(task_id="A"), env.executor) is None
    assert consumer.process(_test_msg(task_id="B"), env.executor) == "tr-1"
    env.trigger_run.assert_called_once()


def test_intermittent_task_does_not_suppress_the_next_task(env):
    # Same for a task Treeherder has already classified: it must not claim a push
    # it will not investigate.
    env.await_skip_reason.side_effect = ["intermittent", None]
    assert consumer.process(_test_msg(task_id="A"), env.executor) is None
    assert consumer.process(_test_msg(task_id="B"), env.executor) == "tr-1"
    env.trigger_run.assert_called_once()


def test_missing_git_mapping_still_triggers_run(env):
    # Unlike a build failure, the run is still useful (the agent works from the
    # task id), so a revision Lando has not mirrored yet must not drop it.
    env.hg_to_git.return_value = None
    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    env.trigger_run.assert_called_once()
    _, ctx = env.executor.submit.call_args.args
    assert ctx.git_commit == ""


def test_unwatched_project_test_skipped(env):
    assert consumer.process(_test_msg(project="try"), env.executor) is None
    env.get_task.assert_not_called()
    env.failing_groups.assert_not_called()


def test_test_repair_trigger_failure_releases_group_for_retry(env):
    env.trigger_run.side_effect = [RuntimeError("boom"), "tr-2"]
    assert consumer.process(_test_msg(task_id="A"), env.executor) is None
    assert consumer.process(_test_msg(task_id="B"), env.executor) == "tr-2"
    assert env.trigger_run.call_count == 2


def test_multiple_failing_groups_trigger_one_run_per_task(env):
    groups = ["dom/base/test/mochitest.ini", "layout/test/mochitest.ini"]
    env.failing_groups.return_value = groups

    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    # The whole task gets a single run; the agent investigates every failing group.
    env.trigger_run.assert_called_once()
    assert env.trigger_run.call_args.args[0]["failure_tasks"] == {
        "test-linux1804-64/opt-mochitest-browser-chrome-1": "TT"
    }
    assert env.executor.submit.call_count == 1
    # Every failing group is named, not an arbitrary one of them.
    _, ctx = env.executor.submit.call_args.args
    assert ctx.test_groups == groups


def test_missing_hg_revision_skips_test_task(env):
    env.get_task.return_value = _task_def(revision=None)
    assert consumer.process(_test_msg(), env.executor) is None
    # Bail before doing the (network-heavy) group resolution.
    env.failing_groups.assert_not_called()
    env.trigger_run.assert_not_called()


def test_every_failing_group_reaches_the_mozci_walk(env):
    groups = ["a/mochitest.ini", "b/mochitest.ini"]
    env.failing_groups.return_value = groups
    env.new_test_failures.side_effect = lambda p, r, cfg, gs, abort=None: {
        "b/mochitest.ini"
    }

    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    assert env.new_test_failures.call_args.args[3] == groups
    _, ctx = env.executor.submit.call_args.args
    assert ctx.test_groups == ["b/mochitest.ini"]


def test_queue_name_includes_non_production_environment():
    with patch.object(consumer.settings, "environment", "development"):
        (queue,) = consumer._build_queues("guest")
    assert queue.name == "queue/guest/build-repair-development-task-failed"


def test_queue_name_omits_production_environment():
    with patch.object(consumer.settings, "environment", "production"):
        (queue,) = consumer._build_queues("guest")
    assert queue.name == "queue/guest/build-repair-task-failed"


def test_classification_landing_during_the_check_cancels_the_run(env):
    # The regression check takes minutes, which is about how long Treeherder needs
    # to classify an intermittent; a verdict that arrives meanwhile must win.
    env.recheck_skip_reason.return_value = "intermittent"
    assert consumer.process(_test_msg(), env.executor) is None
    env.trigger_run.assert_not_called()


def test_recheck_happens_after_the_regression_check(env):
    order = []
    env.new_test_failures.side_effect = lambda p, r, cfg, gs, abort=None: (
        order.append("walk"),
        set(gs),
    )[1]
    env.recheck_skip_reason.side_effect = lambda p, t: order.append("recheck")

    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    assert order == ["walk", "recheck"]


def test_backfill_in_a_new_task_group_is_deduped(env):
    # Backfills and retriggers are dispatched by action tasks, which start their own
    # Taskcluster task group. The same push+group must still be investigated once.
    assert consumer.process(_test_msg(task_id="A", group_id="G1"), env.executor)
    assert (
        consumer.process(_test_msg(task_id="B", group_id="ACTION-GROUP"), env.executor)
        is None
    )
    env.trigger_run.assert_called_once()


def test_different_pushes_are_not_deduped(env):
    # Dedupe is per push: a different manifest newly failing on a later push is a
    # separate regression and must be investigated again.
    _consecutive_pushes(env, "rev-one", "rev-two")
    consumer.process(_test_msg(task_id="A"), env.executor)
    env.failing_groups.return_value = ["other/test/mochitest.ini"]
    consumer.process(_test_msg(task_id="B"), env.executor)
    assert env.trigger_run.call_count == 2


def test_verdict_is_awaited_before_resolving_groups(env):
    # The wait for a verdict is the cheap gate, so it must come first: an intermittent
    # should cost neither a group fetch nor an ancestor walk.
    order = []
    env.await_skip_reason.side_effect = lambda p, t, j: order.append("await")
    env.failing_groups.side_effect = lambda *_: (order.append("groups"), [_GROUP])[1]

    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    assert order == ["await", "groups"]


def test_the_verdict_is_awaited_once_on_every_path(env):
    # A task with no group results used to run its own second wait; the up-front one
    # covers it, and waiting twice would double the delay before a real repair.
    env.failing_groups.side_effect = consumer.treeherder.GroupResultsUnavailable("none")
    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    env.await_skip_reason.assert_called_once()


def test_group_less_intermittent_is_dropped_by_the_up_front_gate(env):
    # The only filter such a failure gets, since it has no manifest to compare.
    env.await_skip_reason.return_value = "intermittent"
    env.failing_groups.side_effect = consumer.treeherder.GroupResultsUnavailable("none")
    assert consumer.process(_test_msg(), env.executor) is None
    env.trigger_run.assert_not_called()


def test_the_job_is_passed_to_the_verdict_wait(env):
    # The wait needs the ingested job: it is the verdict as of ingestion, and without
    # it the wait cannot tell "not classified yet" from "never ingested".
    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    assert env.await_skip_reason.call_args.args[2] is env.job_for_task.return_value


def test_backfilled_test_task_is_skipped(env):
    # Same rule as the build path: a backfill or retrigger re-runs work the push
    # already scheduled, so it is not a new failure to investigate.
    env.get_task.return_value = _task_def(parent="ACTION-CALLBACK")
    assert consumer.process(_test_msg(), env.executor) is None
    env.failing_groups.assert_not_called()
    env.trigger_run.assert_not_called()


def test_stale_push_skips_a_test_failure(env, fresh_push):
    # A test failure surfacing days after its push is not worth repairing either,
    # and the check must precede the ancestor walk, which can block for an hour.
    fresh_push.return_value = True
    assert consumer.process(_test_msg(), env.executor) is None
    fresh_push.assert_called_once_with(
        "autoland", "hgrev", consumer.settings.max_push_age_hours * 3600
    )
    env.new_test_failures.assert_not_called()
    env.trigger_run.assert_not_called()


def test_group_less_task_claims_the_push_for_every_path(env):
    # One run per push whichever path triggered it: a task-level failure (no group
    # results) and a manifest failure on the same push must not both run.
    env.failing_groups.side_effect = [
        consumer.treeherder.GroupResultsUnavailable("none"),
        [_GROUP],
    ]
    assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    assert consumer.process(_test_msg(task_id="B"), env.executor) is None
    env.trigger_run.assert_called_once()


def test_manifest_failure_claims_the_push_against_a_group_less_task(env):
    # The reverse order must dedupe too.
    env.failing_groups.side_effect = [
        [_GROUP],
        consumer.treeherder.GroupResultsUnavailable("none"),
    ]
    assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    assert consumer.process(_test_msg(task_id="B"), env.executor) is None
    env.trigger_run.assert_called_once()


_LINK = (
    "https://treeherder.mozilla.org/#/jobs"
    "?repo=autoland&revision=hgrev&selectedTaskRun=TT"
)


def test_a_rejected_task_is_logged_with_a_treeherder_link(env, caplog):
    # The reason to log links at all: every verdict must be checkable in the UI.
    env.await_skip_reason.return_value = "intermittent"
    with caplog.at_level(logging.INFO, logger="app.consumer"):
        assert consumer.process(_test_msg(), env.executor) is None
    assert _LINK in caplog.text


def test_a_triggered_run_is_logged_with_a_treeherder_link(env, caplog):
    with caplog.at_level(logging.INFO, logger="app.consumer"):
        assert consumer.process(_test_msg(), env.executor) == "tr-1"
    assert _LINK in caplog.text


def test_an_action_scheduled_task_is_logged_with_a_treeherder_link(env, caplog):
    # This one is logged before the revision was previously read, so it is the case
    # the ordering change exists to cover.
    env.get_task.return_value = _task_def(parent="ACTION-CALLBACK")
    with caplog.at_level(logging.INFO, logger="app.consumer"):
        assert consumer.process(_test_msg(), env.executor) is None
    assert _LINK in caplog.text


def test_runs_stop_at_the_daily_limit(env, monkeypatch):
    monkeypatch.setattr(consumer.settings, "max_test_repairs_per_day", 2)
    revisions = iter(["rev-1", "rev-2", "rev-3"])
    env.get_task.side_effect = lambda task_id: _task_def(next(revisions))
    _distinct_groups(env)

    assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    assert consumer.process(_test_msg(task_id="B"), env.executor) == "tr-1"
    assert consumer.process(_test_msg(task_id="C"), env.executor) is None
    assert env.trigger_run.call_count == 2


def test_the_limit_is_a_rolling_window(env, monkeypatch):
    # A run that has aged out of the window frees its slot again.
    monkeypatch.setattr(consumer.settings, "max_test_repairs_per_day", 1)
    revisions = iter(["rev-1", "rev-2"])
    env.get_task.side_effect = lambda task_id: _task_def(next(revisions))
    _distinct_groups(env)

    assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    consumer._test_run_times[0] -= consumer._RATE_WINDOW_SECONDS + 1
    assert consumer.process(_test_msg(task_id="B"), env.executor) == "tr-1"


def test_a_spent_budget_stops_before_any_treeherder_work(env, monkeypatch):
    monkeypatch.setattr(consumer.settings, "max_test_repairs_per_day", 1)
    revisions = iter(["rev-1", "rev-2"])
    env.get_task.side_effect = lambda task_id: _task_def(next(revisions))

    assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    env.job_for_task.reset_mock()
    env.failing_groups.reset_mock()
    assert consumer.process(_test_msg(task_id="B"), env.executor) is None
    env.job_for_task.assert_not_called()
    env.failing_groups.assert_not_called()


def test_a_failed_trigger_gives_its_slot_back(env, monkeypatch):
    # The budget counts runs that started, not attempts.
    monkeypatch.setattr(consumer.settings, "max_test_repairs_per_day", 1)
    env.trigger_run.side_effect = [RuntimeError("boom"), "tr-2"]
    revisions = iter(["rev-1", "rev-2"])
    env.get_task.side_effect = lambda task_id: _task_def(next(revisions))

    assert consumer.process(_test_msg(task_id="A"), env.executor) is None
    assert consumer.process(_test_msg(task_id="B"), env.executor) == "tr-2"


def test_a_budget_blocked_task_does_not_claim_its_push(env, monkeypatch):
    # Same rule as an intermittent: a push we did not investigate stays open, so it
    # is still eligible once the budget frees up.
    monkeypatch.setattr(consumer.settings, "max_test_repairs_per_day", 1)
    revisions = iter(["rev-1", "rev-2", "rev-2"])
    env.get_task.side_effect = lambda task_id: _task_def(next(revisions))
    env.failing_groups.side_effect = [[_GROUP], ["other/mochitest.ini"]] * 2

    assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    assert consumer.process(_test_msg(task_id="B"), env.executor) is None
    monkeypatch.setattr(consumer.settings, "max_test_repairs_per_day", 2)
    assert consumer.process(_test_msg(task_id="B2"), env.executor) == "tr-1"


def test_the_limit_does_not_apply_to_build_repair(env, monkeypatch):
    # The cap is on test-repair; build failures are far rarer and cheaper to judge.
    monkeypatch.setattr(consumer.settings, "max_test_repairs_per_day", 0)
    with (
        patch.object(consumer.treeherder, "recheck_skip_reason", return_value=None),
        patch.object(consumer.regression, "is_new_build_failure", return_value=True),
        patch.object(consumer.lando, "hg_to_git", return_value="gitH"),
        patch.object(consumer.client, "trigger_run", return_value="br-1") as trigger,
    ):
        assert consumer.process(_build_msg(), env.executor) == "br-1"
    trigger.assert_called_once()


def test_exhausting_the_budget_is_logged_once(env, monkeypatch, caplog):
    monkeypatch.setattr(consumer.settings, "max_test_repairs_per_day", 1)
    with caplog.at_level(logging.WARNING, logger="app.consumer"):
        assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    assert (
        sum(
            "budget of 1 runs per 24h is now spent" in r.message for r in caplog.records
        )
        == 1
    )


def test_the_reservation_is_the_hard_boundary(monkeypatch):
    # The early check can pass while another thread takes the last slot, so the
    # reservation itself has to enforce the limit rather than lean on that check.
    monkeypatch.setattr(consumer.settings, "max_test_repairs_per_day", 2)
    assert consumer._reserve_test_run() is True
    assert consumer._reserve_test_run() is True
    assert consumer._reserve_test_run() is False


def test_losing_the_claim_race_gives_the_slot_back(env, monkeypatch):
    # Another task claims the push while this one is being checked. The slot this
    # one reserved must not stay spent for the rest of the day.
    monkeypatch.setattr(consumer.settings, "max_test_repairs_per_day", 1)

    def claim_meanwhile(project, rev, config, groups, abort=None):
        consumer._claim_push(rev)
        return set(groups)

    env.new_test_failures.side_effect = claim_meanwhile
    assert consumer.process(_test_msg(task_id="A"), env.executor) is None
    env.trigger_run.assert_not_called()
    assert len(consumer._test_run_times) == 0


def test_source_test_tasks_are_not_routed_to_test_repair(env):
    # source-test is mostly lint / shadow-scheduler / file-metadata work, closer to a
    # build than to a test, so the agent has no way to repair it by re-running a test.
    body = _test_msg()
    body["task"]["tags"]["kind"] = "source-test"
    body["task"]["tags"]["label"] = "source-test-node-newtab-unit-tests"
    body["task"]["tags"].pop("test-suite", None)

    assert consumer.process(body, env.executor) is None
    env.get_task.assert_not_called()
    env.trigger_run.assert_not_called()


def test_group_less_task_inherited_from_an_ancestor_is_skipped(env):
    env.failing_groups.side_effect = consumer.treeherder.GroupResultsUnavailable("none")
    env.is_new_task_failure.return_value = False

    assert consumer.process(_test_msg(), env.executor) is None
    env.trigger_run.assert_not_called()
    assert env.is_new_task_failure.call_args.args[:3] == (
        "autoland",
        "hgrev",
        "test-linux1804-64/opt-mochitest-browser-chrome-1",
    )


def test_group_less_task_new_at_this_push_still_triggers(env):
    env.failing_groups.side_effect = consumer.treeherder.GroupResultsUnavailable("none")
    env.is_new_task_failure.return_value = True

    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    env.trigger_run.assert_called_once()


def test_an_unreadable_group_lookup_does_not_wait_on_an_ancestor(env):
    # A Treeherder error is not the group-less case: comparing the label would hit
    # the same broken API, so that failure still runs the agent outright.
    env.failing_groups.side_effect = RuntimeError("treeherder down")

    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    env.is_new_task_failure.assert_not_called()


def test_group_less_suites_are_investigated_as_a_whole_task(env):
    # gtest / junit / talos and the rest report no manifests, so there is no group to
    # compare -- but the agent can still find the culprit commit, so they go through the
    # whole-task comparison rather than being dropped.
    env.failing_groups.side_effect = consumer.treeherder.GroupResultsUnavailable("none")
    env.is_new_task_failure.return_value = True
    body = _test_msg()
    body["task"]["tags"]["label"] = "test-macosx1500-aarch64/debug-gtest-1proc"

    assert consumer.process(body, env.executor) == "tr-1"
    env.is_new_task_failure.assert_called_once()


def test_manifest_suites_are_still_investigated(env):
    # The guard must not swallow a suite that does report manifests.
    body = _test_msg()
    body["task"]["tags"]["label"] = "test-linux2404-64/debug-mochitest-browser-chrome-7"
    assert consumer.process(body, env.executor) == "tr-1"


def test_a_walk_is_abandoned_once_the_push_is_claimed(env):
    # The biggest observed waste: a sibling task walking for the full wait only to
    # find the push already handed off. The walk is told to stop instead.
    aborted = {}

    def walk(project, rev, config, groups, should_abort=None):
        consumer._claim_push(rev)
        aborted["stopped"] = should_abort()
        raise consumer.regression.WalkAborted("group at rev")

    env.new_test_failures.side_effect = walk
    assert consumer.process(_test_msg(), env.executor) is None
    assert aborted["stopped"] is True
    env.trigger_run.assert_not_called()


def test_an_abandoned_walk_does_not_look_inherited(env, caplog):
    # It must not be reported as "no new groups": that would read as a real verdict
    # about the failure rather than "we stopped asking".
    env.new_test_failures.side_effect = consumer.regression.WalkAborted("group at rev")
    with caplog.at_level(logging.INFO, logger="app.consumer"):
        assert consumer.process(_test_msg(), env.executor) is None
    assert "abandoning the check" in caplog.text
    assert "inherited" not in caplog.text
    assert "No new, non-intermittent groups" not in caplog.text


def test_the_group_less_walk_is_also_abandoned(env):
    env.failing_groups.side_effect = consumer.treeherder.GroupResultsUnavailable("none")
    env.is_new_task_failure.side_effect = consumer.regression.WalkAborted("task at rev")
    assert consumer.process(_test_msg(), env.executor) is None
    env.trigger_run.assert_not_called()


def _known_intermittent(*bug_ids):
    return consumer.treeherder.IntermittentMatch(list(bug_ids), known=True)


def test_a_known_intermittent_bug_skips_before_waiting_for_a_verdict(env):
    env.intermittent_match.return_value = _known_intermittent(2016093)
    assert consumer.process(_test_msg(), env.executor) is None
    env.await_skip_reason.assert_not_called()
    env.failing_groups.assert_not_called()
    env.trigger_run.assert_not_called()


def test_the_skipped_bug_is_logged(env, caplog):
    env.intermittent_match.return_value = _known_intermittent(2016093)
    with caplog.at_level(logging.INFO, logger="app.consumer"):
        assert consumer.process(_test_msg(), env.executor) is None
    assert "2016093" in caplog.text
    assert _LINK in caplog.text


def test_the_ingested_job_is_what_the_gate_reads(env):
    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    assert env.intermittent_match.call_args.args == (
        "autoland",
        env.job_for_task.return_value,
    )


def test_a_known_intermittent_does_not_claim_its_push(env):
    env.intermittent_match.side_effect = [
        _known_intermittent(2016093),
        consumer.treeherder.IntermittentMatch(),
    ]
    assert consumer.process(_test_msg(task_id="A"), env.executor) is None
    assert consumer.process(_test_msg(task_id="B"), env.executor) == "tr-1"


def test_no_intermittent_match_still_runs(env):
    env.intermittent_match.return_value = consumer.treeherder.IntermittentMatch()
    assert consumer.process(_test_msg(), env.executor) == "tr-1"


def _consecutive_pushes(env, *revisions):
    """Make each message look like it came from a different push."""
    it = iter(revisions)
    env.get_task.side_effect = lambda task_id: _task_def(next(it))


def test_the_same_manifest_on_later_pushes_is_deduped(env):
    _consecutive_pushes(env, "rev-one", "rev-two", "rev-three")
    assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    assert consumer.process(_test_msg(task_id="B"), env.executor) is None
    assert consumer.process(_test_msg(task_id="C"), env.executor) is None
    env.trigger_run.assert_called_once()


def test_a_new_manifest_on_a_later_push_still_runs(env):
    _consecutive_pushes(env, "rev-one", "rev-two")
    assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    env.failing_groups.return_value = ["layout/style/test/mochitest.toml"]
    assert consumer.process(_test_msg(task_id="B"), env.executor) == "tr-1"
    assert env.trigger_run.call_count == 2


def test_one_unseen_manifest_is_enough_to_run(env):
    _consecutive_pushes(env, "rev-one", "rev-two")
    assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    env.failing_groups.return_value = [_GROUP, "layout/style/test/mochitest.toml"]
    assert consumer.process(_test_msg(task_id="B"), env.executor) == "tr-1"


def test_manifest_dedupe_is_per_project():
    consumer._claim_groups("autoland", [_GROUP])
    assert consumer._groups_claimed("autoland", [_GROUP]) is True
    assert consumer._groups_claimed("mozilla-central", [_GROUP]) is False


def test_a_skipped_task_does_not_claim_its_manifests(env):
    _consecutive_pushes(env, "rev-one", "rev-two")
    env.await_skip_reason.side_effect = ["intermittent", None]
    assert consumer.process(_test_msg(task_id="A"), env.executor) is None
    assert consumer.process(_test_msg(task_id="B"), env.executor) == "tr-1"


def test_a_failed_trigger_releases_the_manifests(env):
    _consecutive_pushes(env, "rev-one", "rev-two")
    env.trigger_run.side_effect = [RuntimeError("boom"), "tr-2"]
    assert consumer.process(_test_msg(task_id="A"), env.executor) is None
    assert consumer.process(_test_msg(task_id="B"), env.executor) == "tr-2"


def test_a_group_less_task_is_not_suppressed_by_the_manifest_cache(env):
    _consecutive_pushes(env, "rev-one", "rev-two")
    assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    env.failing_groups.side_effect = consumer.treeherder.GroupResultsUnavailable("none")
    assert consumer.process(_test_msg(task_id="B"), env.executor) == "tr-1"


def test_the_deduped_task_is_logged_with_a_treeherder_link(env, caplog):
    _consecutive_pushes(env, "rev-one", "hgrev")
    assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    with caplog.at_level(logging.INFO, logger="app.consumer"):
        assert consumer.process(_test_msg(task_id="TT"), env.executor) is None
    assert "already investigated on a recent push" in caplog.text
    assert _LINK in caplog.text


def test_a_manifest_dedupe_costs_no_recheck(env):
    _consecutive_pushes(env, "rev-one", "rev-two")
    assert consumer.process(_test_msg(task_id="A"), env.executor) == "tr-1"
    env.recheck_skip_reason.reset_mock()
    assert consumer.process(_test_msg(task_id="B"), env.executor) is None
    env.recheck_skip_reason.assert_not_called()


def _distinct_groups(env):
    """Give every task its own failing manifest, so only the gate under test applies."""
    env.failing_groups.side_effect = lambda project, rev, task_id: [
        f"{task_id}/mochitest.ini"
    ]


def test_test_verify_tasks_are_skipped(env):
    label = "test-linux2404-64/opt-test-verify"
    assert consumer.process(_test_msg(label=label), env.executor) is None
    env.job_for_task.assert_not_called()
    env.trigger_run.assert_not_called()


def test_a_chunked_test_verify_task_is_skipped(env):
    label = "test-linux64/opt-test-verify-wpt-1"
    assert consumer.process(_test_msg(label=label), env.executor) is None
    env.trigger_run.assert_not_called()


def test_an_ordinary_task_is_not_mistaken_for_test_verify(env):
    assert consumer.process(_test_msg(), env.executor) == "tr-1"


def _build_env(monkeypatch, reason=None, introduced=True):
    trigger = MagicMock(return_value="run-1")
    monkeypatch.setattr(consumer.taskcluster, "get_task", lambda tid: _task_def())
    monkeypatch.setattr(consumer.lando, "hg_to_git", lambda rev: "deadbeef")
    walk = MagicMock(return_value=introduced)
    monkeypatch.setattr(consumer.regression, "is_new_build_failure", walk)
    monkeypatch.setattr(
        consumer.treeherder, "recheck_skip_reason", MagicMock(return_value=reason)
    )
    monkeypatch.setattr(consumer.client, "trigger_run", trigger)
    return SimpleNamespace(trigger=trigger, walk=walk, executor=MagicMock())


def test_an_infra_build_failure_is_skipped(monkeypatch):
    env = _build_env(monkeypatch, reason="infra")
    assert consumer.process(_build_msg(), env.executor) is None
    env.trigger.assert_not_called()


def test_a_classified_build_failure_is_read_after_the_walk(monkeypatch):
    # Before the walk it would cost the ingest and classification waits the test
    # path pays; after it, Treeherder has had minutes and it is one request.
    env = _build_env(monkeypatch, reason="intermittent")
    assert consumer.process(_build_msg(), env.executor) is None
    env.walk.assert_called_once()


def test_an_unclassified_build_failure_still_runs(monkeypatch):
    env = _build_env(monkeypatch)
    assert consumer.process(_build_msg(), env.executor) == "run-1"


def test_a_clean_test_history_skips_the_classification_wait(env):
    env.has_clean_history.return_value = True
    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    env.await_skip_reason.assert_not_called()


def test_a_test_with_a_failure_history_still_waits_for_the_verdict(env):
    env.has_clean_history.return_value = False
    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    env.await_skip_reason.assert_called_once()


def test_failures_not_attributable_to_tests_still_wait(env):
    # Nothing to look a history up for, so the wait is the only filter left.
    env.failing_tests.return_value = None
    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    env.has_clean_history.assert_not_called()
    env.await_skip_reason.assert_called_once()


def test_a_classification_already_in_hand_is_honoured_without_waiting(env):
    # A clean history says no verdict is coming, not that one already made is to be
    # ignored: an infra failure stays an infra failure.
    env.has_clean_history.return_value = True
    env.job_for_task.return_value = {
        "failure_classification_id": 5,
        "platform": "linux1804-64",
        "platform_option": "opt",
    }
    assert consumer.process(_test_msg(), env.executor) is None
    env.has_clean_history.assert_not_called()
    env.await_skip_reason.assert_not_called()
    env.trigger_run.assert_not_called()


def test_the_history_check_reads_the_task_suite_and_label(env):
    env.has_clean_history.return_value = True
    consumer.process(_test_msg(), env.executor)
    tests, suite, label = env.has_clean_history.call_args.args
    assert tests == ["dom/base/test/test_a.html"]
    assert suite == "mochitest-browser-chrome"
    assert label == "test-linux1804-64/opt-mochitest-browser-chrome-1"
