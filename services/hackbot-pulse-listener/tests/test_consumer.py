import json
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
        patch.object(consumer.treeherder, "skip_reason", return_value=None),
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
        skip_reason=MagicMock(return_value=None),
        recheck_skip_reason=MagicMock(return_value=None),
        await_skip_reason=MagicMock(return_value=None),
        new_test_failures=MagicMock(
            side_effect=lambda p, r, label, groups: set(groups)
        ),
        hg_to_git=MagicMock(return_value="gitH"),
        trigger_run=MagicMock(return_value="tr-1"),
        executor=MagicMock(),
    )
    monkeypatch.setattr(consumer.taskcluster, "get_task", mocks.get_task)
    monkeypatch.setattr(consumer.treeherder, "failing_groups", mocks.failing_groups)
    monkeypatch.setattr(consumer.treeherder, "job_for_task", mocks.job_for_task)
    monkeypatch.setattr(consumer.treeherder, "skip_reason", mocks.skip_reason)
    monkeypatch.setattr(
        consumer.treeherder, "recheck_skip_reason", mocks.recheck_skip_reason
    )
    monkeypatch.setattr(
        consumer.treeherder, "await_skip_reason", mocks.await_skip_reason
    )
    monkeypatch.setattr(
        consumer.regression, "new_test_failures", mocks.new_test_failures
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
    env.skip_reason.return_value = "intermittent"
    assert consumer.process(_test_msg(), env.executor) is None
    env.failing_groups.assert_not_called()
    env.new_test_failures.assert_not_called()
    env.trigger_run.assert_not_called()


def test_unclassified_failure_is_investigated(env):
    # "not classified" / "new failure" leave the decision to the mozci walk.
    env.skip_reason.return_value = None
    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    env.new_test_failures.assert_called_once()


def test_inherited_test_group_skipped(env):
    env.new_test_failures.side_effect = lambda *_: set()
    assert consumer.process(_test_msg(), env.executor) is None
    env.trigger_run.assert_not_called()


def test_same_group_same_push_triggers_once(env):
    consumer.process(_test_msg(task_id="A"), env.executor)
    consumer.process(_test_msg(task_id="B"), env.executor)
    env.trigger_run.assert_called_once()


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


def test_unreadable_group_results_triggers_once_per_task(env):
    env.failing_groups.side_effect = RuntimeError("treeherder down")
    consumer.process(_test_msg(task_id="A"), env.executor)
    consumer.process(_test_msg(task_id="B"), env.executor)
    env.trigger_run.assert_called_once()


def test_rejected_group_is_not_re_evaluated_by_sibling_chunks(env):
    # The dedupe claim is taken before the expensive checks, so a sibling chunk
    # reporting the same group does not repeat the mozci walk to reach the same
    # verdict.
    env.new_test_failures.side_effect = lambda *_: set()
    assert consumer.process(_test_msg(task_id="A"), env.executor) is None
    assert consumer.process(_test_msg(task_id="B"), env.executor) is None
    env.trigger_run.assert_not_called()
    assert env.new_test_failures.call_count == 1


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
    env.new_test_failures.side_effect = lambda p, r, cfg, gs: {"b/mochitest.ini"}

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
    env.new_test_failures.side_effect = lambda p, r, cfg, gs: (
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
    # Dedupe is per push: the same manifest newly failing on a later push is a
    # separate regression and must be investigated again.
    revisions = iter(["rev-one", "rev-two"])
    env.get_task.side_effect = lambda task_id: _task_def(next(revisions))
    consumer.process(_test_msg(task_id="A"), env.executor)
    consumer.process(_test_msg(task_id="B"), env.executor)
    assert env.trigger_run.call_count == 2


def test_whole_task_waits_for_a_verdict_before_triggering(env):
    env.failing_groups.side_effect = consumer.treeherder.GroupResultsUnavailable("none")
    env.await_skip_reason.return_value = "intermittent"
    assert consumer.process(_test_msg(), env.executor) is None
    env.await_skip_reason.assert_called_once()
    env.trigger_run.assert_not_called()


def test_whole_task_triggers_when_no_verdict_arrives(env):
    env.failing_groups.side_effect = consumer.treeherder.GroupResultsUnavailable("none")
    env.await_skip_reason.return_value = None
    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    env.trigger_run.assert_called_once()


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
