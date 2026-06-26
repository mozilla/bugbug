import logging
import time

from app import client, notify
from app.config import settings

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"succeeded", "failed", "timed_out"}


def poll_and_notify(
    run_id: str, revision: str, repo: str, developer_email: str | None
) -> None:
    """Poll the run until terminal, then notify the developer.

    Runs on a background executor thread; never lets an exception escape.
    """
    try:
        run_doc = _poll_until_terminal(run_id)
    except Exception:
        logger.exception("Polling failed for run %s", run_id)
        return

    if run_doc is None:
        logger.warning(
            "Run %s did not finish within %s minutes; giving up",
            run_id,
            settings.run_max_age_minutes,
        )
        return

    try:
        notify.send_email(developer_email, revision, repo, run_id, run_doc)
    except Exception:
        logger.exception("Failed to send notification for run %s", run_id)


def _poll_until_terminal(run_id: str) -> dict | None:
    deadline = time.monotonic() + settings.run_max_age_minutes * 60
    while True:
        run_doc = client.get_run(run_id)
        if run_doc.get("status") in TERMINAL_STATUSES:
            return run_doc
        if time.monotonic() >= deadline:
            return None
        time.sleep(settings.poll_interval_seconds)
