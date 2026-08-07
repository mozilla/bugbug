from unittest.mock import patch

import pytest
from app import worker
from app.models import RunContext

CTX = RunContext(
    run_id="run-1",
    repo="autoland",
    git_commit="deadbeef",
    hg_revision="hg123",
    task_id="T1",
    developer_email="dev@mozilla.com",
)


@pytest.fixture(autouse=True)
def unactioned():
    """Keep the pre-notification re-check off the network; no sheriff acted."""
    with patch.object(
        worker.treeherder, "recheck_skip_reason", return_value=None
    ) as recheck:
        yield recheck


def test_terminal_run_notifies_once():
    run_doc = {"status": "succeeded", "summary": {}}
    with (
        patch.object(worker.client, "get_run", return_value=run_doc) as get_run,
        patch.object(worker, "notify") as notify,
    ):
        worker.poll_and_notify(CTX)

    get_run.assert_called_once()
    notify.send_email.assert_called_once_with(CTX, run_doc, None)


def test_gives_up_after_max_age(monkeypatch):
    monkeypatch.setattr(worker.settings, "run_max_age_minutes", 0)
    with (
        patch.object(
            worker.client, "get_run", return_value={"status": "running"}
        ) as get_run,
        patch.object(worker, "notify") as notify,
    ):
        worker.poll_and_notify(CTX)

    get_run.assert_called_once()
    notify.send_email.assert_not_called()


def test_a_late_sheriff_action_marks_the_notification():
    run_doc = {"status": "succeeded", "summary": {}}
    with (
        patch.object(worker.client, "get_run", return_value=run_doc),
        patch.object(
            worker.treeherder, "recheck_skip_reason", return_value="fixed by commit"
        ),
        patch.object(worker, "notify") as notify,
    ):
        worker.poll_and_notify(CTX)

    notify.send_email.assert_called_once_with(CTX, run_doc, "fixed by commit")


def test_an_unactioned_failure_notifies_unmarked():
    run_doc = {"status": "succeeded", "summary": {}}
    with (
        patch.object(worker.client, "get_run", return_value=run_doc),
        patch.object(worker.treeherder, "recheck_skip_reason", return_value=None),
        patch.object(worker, "notify") as notify,
    ):
        worker.poll_and_notify(CTX)

    notify.send_email.assert_called_once_with(CTX, run_doc, None)


def test_the_analysis_is_still_sent_after_a_sheriff_acted():
    run_doc = {"status": "succeeded", "summary": {}}
    with (
        patch.object(worker.client, "get_run", return_value=run_doc),
        patch.object(
            worker.treeherder, "recheck_skip_reason", return_value="fixed by commit"
        ),
        patch.object(worker, "notify") as notify,
    ):
        worker.poll_and_notify(CTX)

    notify.send_email.assert_called_once()


def test_a_failed_recheck_does_not_block_the_notification():
    run_doc = {"status": "succeeded", "summary": {}}
    with (
        patch.object(worker.client, "get_run", return_value=run_doc),
        patch.object(
            worker.treeherder,
            "recheck_skip_reason",
            side_effect=RuntimeError("treeherder down"),
        ),
        patch.object(worker, "notify") as notify,
    ):
        worker.poll_and_notify(CTX)

    notify.send_email.assert_called_once_with(CTX, run_doc, None)
