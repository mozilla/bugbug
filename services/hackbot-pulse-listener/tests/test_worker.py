from unittest.mock import patch

from app import worker


def test_terminal_run_notifies_once():
    run_doc = {"status": "succeeded", "summary": {}}
    with (
        patch.object(worker.client, "get_run", return_value=run_doc) as get_run,
        patch.object(worker, "notify") as notify,
    ):
        worker.poll_and_notify("run-1", "rev", "try", "dev@mozilla.com")

    get_run.assert_called_once()
    notify.send_email.assert_called_once()
    args = notify.send_email.call_args.args
    assert args == ("dev@mozilla.com", "rev", "try", "run-1", run_doc)


def test_gives_up_after_max_age(monkeypatch):
    monkeypatch.setattr(worker.settings, "run_max_age_minutes", 0)
    with (
        patch.object(
            worker.client, "get_run", return_value={"status": "running"}
        ) as get_run,
        patch.object(worker, "notify") as notify,
    ):
        worker.poll_and_notify("run-1", "rev", "try", "dev@mozilla.com")

    get_run.assert_called_once()
    notify.send_email.assert_not_called()
