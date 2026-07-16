from unittest.mock import patch

from app import regression
from mozci.errors import ParentPushNotFound

LABEL = "build-linux64/opt"


class FakeTask:
    def __init__(self, label=LABEL, state="completed", result="passed"):
        self.label = label
        self.state = state
        self.result = result

    @property
    def failed(self):
        return self.result in ("busted", "failed", "exception")


class FakePush:
    def __init__(self, rev, tasks, parent=None):
        self.rev = rev
        self.tasks = tasks
        self._parent = parent

    @property
    def parent(self):
        if self._parent is None:
            raise ParentPushNotFound("no parent", rev=self.rev, branch="autoland")
        return self._parent


def _passed(label=LABEL):
    return [FakeTask(label=label, result="passed")]


def _failed(label=LABEL):
    return [FakeTask(label=label, result="failed")]


def _infra(label=LABEL):
    return [FakeTask(label=label, state="exception", result="exception")]


def _running(label=LABEL):
    return [FakeTask(label=label, state="running", result=None)]


def _chain(*task_lists):
    """Build a parent chain: task_lists[0] is the observed push, [1] its parent, ..."""
    push = None
    for i, tasks in enumerate(reversed(task_lists)):
        push = FakePush(rev=f"rev{len(task_lists) - 1 - i}", tasks=tasks, parent=push)
    return push


def _run(observed):
    with (
        patch.object(regression, "Push", return_value=observed),
        patch.object(regression.time, "sleep"),
    ):
        return regression.is_new_build_failure("autoland", observed.rev, LABEL)


def _run_polling(observed_sequence):
    """Feed a fresh observed-push chain on each poll, simulating builds settling."""
    with (
        patch.object(regression, "Push", side_effect=observed_sequence),
        patch.object(regression.time, "sleep"),
    ):
        return regression.is_new_build_failure(
            "autoland", observed_sequence[0].rev, LABEL
        )


def test_parent_passed_is_new_failure():
    assert _run(_chain(_failed(), _passed())) is True


def test_parent_failed_is_inherited():
    assert _run(_chain(_failed(), _failed())) is False


def test_parent_intermittent_is_new_failure():
    parent_tasks = [
        FakeTask(result="failed"),
        FakeTask(result="passed"),
    ]
    assert _run(_chain(_failed(), parent_tasks)) is True


def test_coalesced_parent_then_green_grandparent_is_new_failure():
    assert _run(_chain(_failed(), [], _passed())) is True


def test_coalesced_parent_then_failed_grandparent_is_inherited():
    assert _run(_chain(_failed(), [], _failed())) is False


def test_infra_parent_is_waited_then_new_failure():
    # An exceptioned parent build is polled, not skipped; once its retry lands
    # green the observed push introduced the failure.
    assert (
        _run_polling([_chain(_failed(), _infra()), _chain(_failed(), _passed())])
        is True
    )


def test_running_parent_is_waited_then_inherited():
    # A still-running parent is polled until it settles; a failure there means
    # the observed push inherited it.
    assert (
        _run_polling([_chain(_failed(), _running()), _chain(_failed(), _failed())])
        is False
    )


def test_unsettled_parent_past_deadline_runs_agent():
    # An ancestor build that never settles fails open once the deadline passes.
    observed = _chain(_failed(), _running())
    with (
        patch.object(regression, "Push", return_value=observed),
        patch.object(regression.time, "sleep"),
        patch.object(
            regression.time,
            "monotonic",
            side_effect=[0.0, regression.MAX_WAIT_SECONDS + 1],
        ),
    ):
        assert regression.is_new_build_failure("autoland", observed.rev, LABEL) is True


def test_no_parent_runs_agent():
    assert _run(_chain(_failed())) is True


def test_no_decisive_ancestor_runs_agent():
    empties = [_failed()] + [[] for _ in range(regression.MAX_DEPTH + 2)]
    assert _run(_chain(*empties)) is True


def test_mozci_error_runs_agent():
    with patch.object(regression, "Push", side_effect=RuntimeError("boom")):
        assert regression.is_new_build_failure("autoland", "rev", LABEL) is True


def test_other_label_on_parent_ignored():
    parent_tasks = _failed(label="build-macosx64/opt")  # different build, not ours
    assert _run(_chain(_failed(), parent_tasks)) is True


def test_exception_parent_is_waited_not_failed():
    # A completed task with an infra `exception` result is unsettled, not a
    # failure: we wait for the retry rather than declaring the parent failed.
    exception_parent = [FakeTask(state="completed", result="exception")]
    settled = _chain(_failed(), _passed())
    assert _run_polling([_chain(_failed(), exception_parent), settled]) is True


def test_treeherder_result_vocabulary():
    # success/busted are the Treeherder-source spellings of passed/failed.
    assert _run(_chain(_failed(), [FakeTask(result="success")])) is True
    assert _run(_chain(_failed(), [FakeTask(result="busted")])) is False
