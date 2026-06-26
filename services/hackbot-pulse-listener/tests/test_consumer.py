import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import consumer

FIXTURES = Path(__file__).parent / "fixtures"


def setup_function():
    consumer._seen.clear()


def _sample_bodies():
    data = json.loads((FIXTURES / "pulse_messages.json").read_text())
    # The inspector wraps the real AMQP body under "payload".
    return [m["payload"] for m in data]


def _build_msg(task_id="ABC", project="try", label="build-linux64/opt"):
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
        patch.object(consumer.taskcluster, "get_revision") as get_rev,
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        for body in _sample_bodies():
            assert consumer.process(body, executor) is None
    get_rev.assert_not_called()
    trigger.assert_not_called()
    executor.submit.assert_not_called()


def test_build_failure_triggers_run_and_submits_poll():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_revision", return_value="deadbeef"),
        patch.object(consumer.client, "trigger_run", return_value="run-1") as trigger,
    ):
        run_id = consumer.process(_build_msg(), executor)

    assert run_id == "run-1"
    trigger.assert_called_once()
    inputs = trigger.call_args.args[0]
    assert inputs["git_commit"] == "deadbeef"
    assert inputs["failure_tasks"] == {"build-linux64/opt": "ABC"}
    executor.submit.assert_called_once()


def test_same_revision_triggers_once():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_revision", return_value="deadbeef"),
        patch.object(consumer.client, "trigger_run", return_value="run-1") as trigger,
    ):
        consumer.process(_build_msg(task_id="T1"), executor)
        consumer.process(_build_msg(task_id="T2"), executor)

    trigger.assert_called_once()


def test_unwatched_project_skipped_before_api_call():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_revision") as get_rev,
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        assert consumer.process(_build_msg(project="mozilla-central"), executor) is None

    get_rev.assert_not_called()
    trigger.assert_not_called()


def test_trigger_failure_releases_revision_for_retry():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_revision", return_value="deadbeef"),
        patch.object(
            consumer.client, "trigger_run", side_effect=[RuntimeError("boom"), "run-2"]
        ) as trigger,
    ):
        assert consumer.process(_build_msg(task_id="T1"), executor) is None
        # Same revision can be retried because the failed claim was released.
        assert consumer.process(_build_msg(task_id="T2"), executor) == "run-2"

    assert trigger.call_count == 2
