"""Email the person who requested a run when it reaches a terminal state.

A run launched from the UI carries its requester's email. Once the run has
succeeded, failed or timed out, that address gets a short note with the
outcome and a link to the run page, so nobody has to keep a tab open. Runs
with no requester (triggered by webhooks) are skipped.

This module only composes and delivers the message. It keeps no state, so
calling it once per run is the caller's job. Delivery is best-effort: a
failed send is logged, never raised. Only the requester is addressed; this
is a personal ping, not a report.
"""

from __future__ import annotations

import asyncio
import logging

import markdown2
import sendgrid
from sendgrid.helpers.mail import Content, From, HtmlContent, Mail, Subject, To

from app.config import settings
from app.database.models import Run

log = logging.getLogger(__name__)


def run_url(run_id: str) -> str:
    return f"{settings.ui_base_url.rstrip('/')}/runs/{run_id}"


def build_message(run: Run) -> tuple[str, str]:
    """Compose the subject and Markdown body of the notice for ``run``."""
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


def _recipient(run: Run) -> str:
    return settings.notification_override_email.strip() or run.requested_by


def _send_sync(recipient: str, subject: str, body_md: str) -> int:
    message = Mail(
        From(settings.notification_sender),
        To(recipient),
        Subject(subject),
        Content("text/plain", body_md),
        HtmlContent(markdown2.markdown(body_md, extras=["fenced-code-blocks"])),
    )
    client = sendgrid.SendGridAPIClient(api_key=settings.sendgrid_api_key)
    response = client.send(message=message)
    return response.status_code


async def notify_requester(run: Run) -> bool:
    """Mail the run's requester about its terminal state. Returns whether it sent.

    No requester is a quiet no-op; a delivery failure is logged and swallowed.
    """
    if not run.requested_by:
        return False

    recipient = _recipient(run)
    subject, body = build_message(run)
    try:
        status_code = await asyncio.to_thread(_send_sync, recipient, subject, body)
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
