from unittest.mock import patch

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


def test_terminal_run_notifies_once():
    run_doc = {"status": "succeeded", "summary": {}}
    with (
        patch.object(worker.client, "get_run", return_value=run_doc) as get_run,
        patch.object(worker, "notify") as notify,
    ):
        worker.poll_and_notify(CTX)

    get_run.assert_called_once()
    notify.send_email.assert_called_once_with(CTX, run_doc)


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
