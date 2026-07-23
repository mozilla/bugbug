import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import consumer
from app.failures import FailingGroup
from app.flakiness import Flakiness

FIXTURES = Path(__file__).parent / "fixtures"


def setup_function():
    consumer._seen.clear()
    consumer._seen_tests.clear()


def _sample_bodies():
    data = json.loads((FIXTURES / "pulse_messages.json").read_text())
    # The inspector wraps the real AMQP body under "payload".
    return [m["payload"] for m in data]


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


def test_sample_messages_route_to_test_repair_not_build():
    # The captured samples are all test tasks; watched ones now reach the test-repair
    # path (failing_groups consulted), and none trigger the build-repair agent.
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
        patch.object(consumer.failures, "failing_groups", return_value=[]) as fg,
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        for body in _sample_bodies():
            assert consumer.process(body, executor) is None
    # At least the autoland test samples were routed to the test-repair path.
    assert fg.called
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
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
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
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
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
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
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
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
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
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
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
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
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
        patch.object(consumer.taskcluster, "get_hg_revision") as get_rev,
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        assert consumer.process(_build_msg(project="try"), executor) is None

    get_rev.assert_not_called()
    trigger.assert_not_called()


def test_unmappable_revision_skipped():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
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
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
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


_GROUP = FailingGroup(
    group="dom/base/test/mochitest.ini",
    test="dom/base/test/test_a.js",
    failure_type="GENERIC",
)


def _test_repair_patches(is_new=(True, "greenrev"), rate=0.0):
    return (
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
        patch.object(consumer.failures, "failing_groups", return_value=[_GROUP]),
        patch.object(
            consumer.flakiness,
            "get_flakiness",
            return_value=Flakiness(
                total=10, passes=int(10 * (1 - rate)), fails=int(10 * rate)
            ),
        ),
        patch.object(consumer.regression, "is_new_test_failure", return_value=is_new),
        patch.object(consumer.lando, "hg_to_git", return_value="gitH"),
    )


def test_test_failure_triggers_rca_run():
    executor = MagicMock()
    p1, p2, p3, p4, p5 = _test_repair_patches()
    with (
        p1,
        p2,
        p3,
        p4,
        p5,
        patch.object(consumer.client, "trigger_run", return_value="tr-1") as trigger,
    ):
        run_id = consumer.process(_test_msg(), executor)

    assert run_id == "tr-1"
    trigger.assert_called_once()
    inputs = trigger.call_args.args[0]
    assert trigger.call_args.kwargs["agent_name"] == "test-repair"
    # The agent resolves the test, commit range and clone depth itself; the
    # listener only hands it the failing task.
    assert inputs["failure_tasks"] == {
        "test-linux1804-64/opt-mochitest-browser-chrome-1": "TT"
    }
    assert "test_id" not in inputs
    assert "candidate_commits" not in inputs
    fn, ctx = executor.submit.call_args.args
    assert fn is consumer.worker.poll_and_notify
    assert ctx.agent == "test-repair"
    assert ctx.test_name == "dom/base/test/mochitest.ini"


def test_intermittent_test_skipped_before_mozci():
    executor = MagicMock()
    p1, p2, p3, p4, p5 = _test_repair_patches(rate=0.8)
    with (
        p1,
        p2,
        p3,
        p4 as is_new,
        p5,
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        assert consumer.process(_test_msg(), executor) is None
    is_new.assert_not_called()
    trigger.assert_not_called()


def test_inherited_test_group_skipped():
    executor = MagicMock()
    p1, p2, p3, p4, p5 = _test_repair_patches(is_new=(False, None))
    with (
        p1,
        p2,
        p3,
        p4,
        p5,
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        assert consumer.process(_test_msg(), executor) is None
    trigger.assert_not_called()


def test_same_group_same_push_triggers_once():
    executor = MagicMock()
    p1, p2, p3, p4, p5 = _test_repair_patches()
    with (
        p1,
        p2,
        p3,
        p4,
        p5,
        patch.object(consumer.client, "trigger_run", return_value="tr-1") as trigger,
    ):
        consumer.process(_test_msg(task_id="A"), executor)
        consumer.process(_test_msg(task_id="B"), executor)
    trigger.assert_called_once()


def test_no_failing_groups_skips():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
        patch.object(consumer.failures, "failing_groups", return_value=[]),
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        assert consumer.process(_test_msg(), executor) is None
    trigger.assert_not_called()


def test_unwatched_project_test_skipped():
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_hg_revision") as get_rev,
        patch.object(consumer.failures, "failing_groups") as fg,
    ):
        assert consumer.process(_test_msg(project="try"), executor) is None
    get_rev.assert_not_called()
    fg.assert_not_called()


def test_test_repair_trigger_failure_releases_group_for_retry():
    executor = MagicMock()
    p1, p2, p3, p4, p5 = _test_repair_patches()
    with (
        p1,
        p2,
        p3,
        p4,
        p5,
        patch.object(
            consumer.client, "trigger_run", side_effect=[RuntimeError("boom"), "tr-2"]
        ) as trigger,
    ):
        assert consumer.process(_test_msg(task_id="A"), executor) is None
        assert consumer.process(_test_msg(task_id="B"), executor) == "tr-2"
    assert trigger.call_count == 2


def test_harness_detection():
    # xpcshell via the test-suite tag, and via the label when the suite is generic.
    assert consumer._harness({"test-suite": "xpcshell"}, "irrelevant") == "xpcshell"
    assert (
        consumer._harness({"test-suite": "test"}, "test-linux/opt-xpcshell-4")
        == "xpcshell"
    )
    assert (
        consumer._harness({"test-suite": "mochitest-browser-chrome"}, "l")
        == "mochitest"
    )
    # Falls back to the kind when neither xpcshell nor mochitest matches.
    assert (
        consumer._harness({"kind": "web-platform-tests"}, "l") == "web-platform-tests"
    )


def test_multiple_failing_groups_trigger_one_run_per_task(monkeypatch):
    executor = MagicMock()
    groups = [
        FailingGroup("dom/base/test/mochitest.ini", "dom/base/test/a.js", "GENERIC"),
        FailingGroup("layout/test/mochitest.ini", "layout/test/b.js", "TIMEOUT"),
    ]
    with (
        patch.object(consumer.taskcluster, "get_hg_revision", return_value="hgrev"),
        patch.object(consumer.failures, "failing_groups", return_value=groups),
        patch.object(
            consumer.flakiness,
            "get_flakiness",
            return_value=Flakiness(total=10, passes=10),
        ),
        patch.object(
            consumer.regression,
            "is_new_test_failure",
            return_value=(True, "greenrev"),
        ),
        patch.object(consumer.lando, "hg_to_git", return_value="gitH"),
        patch.object(consumer.client, "trigger_run", return_value="tr-1") as trigger,
    ):
        run_id = consumer.process(_test_msg(), executor)

    # The whole task gets a single run; the agent investigates every failing group.
    assert run_id == "tr-1"
    trigger.assert_called_once()
    assert trigger.call_args.args[0]["failure_tasks"] == {
        "test-linux1804-64/opt-mochitest-browser-chrome-1": "TT"
    }
    assert executor.submit.call_count == 1


def test_missing_hg_revision_skips_test_task(monkeypatch):
    executor = MagicMock()
    with (
        patch.object(consumer.taskcluster, "get_hg_revision", return_value=None),
        patch.object(consumer.failures, "failing_groups") as fg,
        patch.object(consumer.client, "trigger_run") as trigger,
    ):
        assert consumer.process(_test_msg(), executor) is None
    # Bail before doing the (network-heavy) group resolution.
    fg.assert_not_called()
    trigger.assert_not_called()


def test_queue_name_includes_non_production_environment():
    with patch.object(consumer.settings, "environment", "development"):
        (queue,) = consumer._build_queues("guest")
    assert queue.name == "queue/guest/build-repair-development-task-failed"


def test_queue_name_omits_production_environment():
    with patch.object(consumer.settings, "environment", "production"):
        (queue,) = consumer._build_queues("guest")
    assert queue.name == "queue/guest/build-repair-task-failed"
