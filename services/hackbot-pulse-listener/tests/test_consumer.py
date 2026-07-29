import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from app import consumer

FIXTURES = Path(__file__).parent / "fixtures"

DECISION_TASK_ID = "DECISION"


def setup_function():
    consumer._seen.clear()


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


def test_sample_messages_are_all_tests_and_skipped():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_task") as get_task,
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        for body in _sample_bodies():
            assert consumer.process(body, executor) is None
    get_task.assert_not_called()
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


def test_queue_name_includes_non_production_environment():
    with patch.object(consumer.settings, "environment", "development"):
        (queue,) = consumer._build_queues("guest")
    assert queue.name == "queue/guest/build-repair-development-task-failed"


def test_queue_name_omits_production_environment():
    with patch.object(consumer.settings, "environment", "production"):
        (queue,) = consumer._build_queues("guest")
    assert queue.name == "queue/guest/build-repair-task-failed"
