import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app import consumer
from app.failures import FailingGroup

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


@pytest.fixture
def env(monkeypatch):
    """The test-repair path's external seams, as named mocks.

    Defaults describe the happy path: one failing group, not intermittent, a new
    regression, revision mappable, trigger succeeds.
    """
    mocks = SimpleNamespace(
        get_hg_revision=MagicMock(return_value="hgrev"),
        failing_groups=MagicMock(return_value=[_GROUP]),
        intermittent_tests=MagicMock(return_value=set()),
        new_test_failures=MagicMock(
            side_effect=lambda p, r, label, groups: set(groups)
        ),
        hg_to_git=MagicMock(return_value="gitH"),
        trigger_run=MagicMock(return_value="tr-1"),
        executor=MagicMock(),
    )
    monkeypatch.setattr(consumer.taskcluster, "get_hg_revision", mocks.get_hg_revision)
    monkeypatch.setattr(consumer.failures, "failing_groups", mocks.failing_groups)
    monkeypatch.setattr(
        consumer.flakiness, "intermittent_tests", mocks.intermittent_tests
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
    assert ctx.test_groups == [_GROUP.group]


def test_intermittent_test_skipped_before_mozci(env):
    env.intermittent_tests.return_value = {_GROUP.test}
    assert consumer.process(_test_msg(), env.executor) is None
    env.new_test_failures.assert_not_called()
    env.trigger_run.assert_not_called()


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


def test_unreadable_errorsummary_still_triggers_run(env):
    # Fail open like the build path: an errorsummary that cannot be read must not
    # be mistaken for "nothing failed" and drop a possible regression.
    env.failing_groups.side_effect = RuntimeError("no artifact")
    assert consumer.process(_test_msg(task_id="ERR"), env.executor) == "tr-1"
    env.trigger_run.assert_called_once()
    # With no groups resolved there is nothing to filter or to name.
    env.intermittent_tests.assert_not_called()
    env.new_test_failures.assert_not_called()
    _, ctx = env.executor.submit.call_args.args
    assert ctx.test_groups == []


def test_unreadable_errorsummary_triggers_once_per_task(env):
    env.failing_groups.side_effect = RuntimeError("no artifact")
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
    env.get_hg_revision.assert_not_called()
    env.failing_groups.assert_not_called()


def test_test_repair_trigger_failure_releases_group_for_retry(env):
    env.trigger_run.side_effect = [RuntimeError("boom"), "tr-2"]
    assert consumer.process(_test_msg(task_id="A"), env.executor) is None
    assert consumer.process(_test_msg(task_id="B"), env.executor) == "tr-2"
    assert env.trigger_run.call_count == 2


def test_multiple_failing_groups_trigger_one_run_per_task(env):
    groups = [
        FailingGroup("dom/base/test/mochitest.ini", "dom/base/test/a.js", "GENERIC"),
        FailingGroup("layout/test/mochitest.ini", "layout/test/b.js", "TIMEOUT"),
    ]
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
    assert ctx.test_groups == [g.group for g in groups]


def test_missing_hg_revision_skips_test_task(env):
    env.get_hg_revision.return_value = None
    assert consumer.process(_test_msg(), env.executor) is None
    # Bail before doing the (network-heavy) group resolution.
    env.failing_groups.assert_not_called()
    env.trigger_run.assert_not_called()


def test_intermittent_gate_runs_before_the_mozci_walk(env):
    # Both gates are batch calls over the task's groups; the cheap one goes first
    # and the expensive one only sees what survived.
    groups = [
        FailingGroup("a/mochitest.ini", "a/test_a.js", "GENERIC"),
        FailingGroup("b/mochitest.ini", "b/test_b.js", "GENERIC"),
    ]
    env.failing_groups.return_value = groups
    env.intermittent_tests.return_value = {"a/test_a.js"}

    assert consumer.process(_test_msg(), env.executor) == "tr-1"
    env.intermittent_tests.assert_called_once()
    tests, suite, label = env.intermittent_tests.call_args.args
    assert list(tests) == ["a/test_a.js", "b/test_b.js"]
    assert suite == "mochitest-browser-chrome"
    # Only the surviving group reaches the walk.
    assert env.new_test_failures.call_args.args[3] == ["b/mochitest.ini"]
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
