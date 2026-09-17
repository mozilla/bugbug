"""Email the person who requested a run when it reaches a terminal state.

A run launched from the UI carries its requester's email; once the run has
succeeded, failed or timed out, that one address gets a short note with the
outcome and a link to the run page, so nobody has to keep a tab open to watch
progress. Runs with no requester (those triggered by webhooks) are skipped:
their results already land where they were asked for.

This module owns only the message and its delivery. It keeps no state and
makes no guarantee of its own about how often it is called; the caller is
responsible for invoking it once per run. Delivery is best-effort: a failure
to send is logged, never raised.

Configured entirely by environment:

``SENDGRID_API_KEY`` / ``NOTIFICATION_SENDER``
    Required; without both, nothing is sent.
``NOTIFICATION_OVERRIDE_EMAIL``
    Replaces the recipient. Keeps a development deployment from mailing real
    people.

The recipient alone is addressed -- no team copy -- since this is a personal
"your run is done" ping rather than a report.
"""

from __future__ import annotations

import asyncio
import logging
import os

import markdown2
import sendgrid
from sendgrid.helpers.mail import Content, From, HtmlContent, Mail, Subject, To

from app.config import settings
from app.database.models import Run

log = logging.getLogger(__name__)


def run_url(run_id: str) -> str:
    return f"{settings.ui_base_url.rstrip('/')}/runs/{run_id}"


def build_message(run: Run) -> tuple[str, str]:
    """(subject, markdown body) for a completion notice about ``run``."""
    outcome = run.status.replace("_", " ")
    subject = f"[Hackbot] {run.agent} run {outcome}"

    body = "\n".join(
        [
            f"Your **{run.agent}** run has **{outcome}**.",
            "",
            f"Open the run: {run_url(str(run.run_id))}",
        ]
    )
    return subject, body


def _recipient(run: Run) -> str | None:
    override = os.environ.get("NOTIFICATION_OVERRIDE_EMAIL", "").strip()
    if override:
        return override
    return run.requested_by or None


def _send_sync(sender: str, recipient: str, subject: str, body_md: str) -> int:
    message = Mail(
        From(sender),
        To(recipient),
        Subject(subject),
        Content("text/plain", body_md),
        HtmlContent(markdown2.markdown(body_md, extras=["fenced-code-blocks"])),
    )
    api_key = os.environ["SENDGRID_API_KEY"]
    response = sendgrid.SendGridAPIClient(api_key=api_key).send(message=message)
    return response.status_code


async def notify_requester(run: Run) -> bool:
    """Mail the run's requester about its terminal state. Returns whether it sent.

    A run with no requester or missing SendGrid config is a quiet no-op; a
    delivery failure is logged and swallowed (see module docstring).
    """
    if not run.requested_by:
        return False

    sender = os.environ.get("NOTIFICATION_SENDER", "").strip()
    if not (os.environ.get("SENDGRID_API_KEY") and sender):
        log.warning(
            "SENDGRID_API_KEY / NOTIFICATION_SENDER not configured; "
            "not notifying %s about run %s",
            run.requested_by,
            run.run_id,
        )
        return False

    recipient = _recipient(run)
    if not recipient:
        return False

    subject, body = build_message(run)
    try:
        status_code = await asyncio.to_thread(
            _send_sync, sender, recipient, subject, body
        )
    except Exception:
        log.exception("Failed to notify %s about run %s", recipient, run.run_id)
        return False
    log.info(
        "Notified %s about run %s (%s): SendGrid %s",
        recipient,
        run.run_id,
        run.status,
        status_code,
    )
    return True
