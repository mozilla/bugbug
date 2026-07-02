import json
from pathlib import Path
from unittest.mock import patch

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
    with (
        patch.object(consumer.taskcluster, "get_hg_revision") as get_rev,
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        for body in _sample_bodies():
            assert consumer.process(body) is None
    get_rev.assert_not_called()
    trigger.assert_not_called()


def test_build_failure_triggers_run():
    with (
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
        patch.object(consumer.lando, "hg_to_git", return_value="deadbeef"),
        patch.object(consumer.client, "trigger_run", return_value="run-1") as trigger,
    ):
        run_id = consumer.process(_build_msg())

    assert run_id == "run-1"
    trigger.assert_called_once()
    inputs = trigger.call_args.args[0]
    assert inputs["git_commit"] == "deadbeef"
    assert inputs["failure_tasks"] == {"build-linux64/opt": "ABC"}


def test_same_revision_triggers_once():
    with (
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
        patch.object(consumer.lando, "hg_to_git", return_value="deadbeef"),
        patch.object(consumer.client, "trigger_run", return_value="run-1") as trigger,
    ):
        consumer.process(_build_msg(task_id="T1"))
        consumer.process(_build_msg(task_id="T2"))

    trigger.assert_called_once()


def test_unwatched_project_skipped_before_api_call():
    with (
        patch.object(consumer.taskcluster, "get_hg_revision") as get_rev,
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        assert consumer.process(_build_msg(project="mozilla-central")) is None

    get_rev.assert_not_called()
    trigger.assert_not_called()


def test_unmappable_revision_skipped():
    with (
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
        patch.object(consumer.lando, "hg_to_git", return_value=None),
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        assert consumer.process(_build_msg()) is None

    trigger.assert_not_called()


def test_trigger_failure_releases_revision_for_retry():
    with (
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
        patch.object(consumer.lando, "hg_to_git", return_value="deadbeef"),
        patch.object(
            consumer.client, "trigger_run", side_effect=[RuntimeError("boom"), "run-2"]
        ) as trigger,
    ):
        assert consumer.process(_build_msg(task_id="T1")) is None
        # Same revision can be retried because the failed claim was released.
        assert consumer.process(_build_msg(task_id="T2")) == "run-2"

    assert trigger.call_count == 2
