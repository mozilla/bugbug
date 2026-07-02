"""Email notifications for completed agent runs.

Sends a best-effort email via SendGrid when a run reaches a terminal
status (succeeded / failed / timed_out), so a developer doesn't have to
keep polling ``GET /runs/{run_id}`` to find out an agent finished.

This is entirely optional: if ``SENDGRID_API_KEY`` or
``NOTIFICATION_SENDER_EMAIL`` aren't configured, or a run was created
without a ``notify_email``, this module is a no-op. Any failure to
send (bad API key, SendGrid outage, etc.) is logged and swallowed --
a broken notification must never fail run reconciliation.
"""

import asyncio
import html
import logging
import re
from functools import lru_cache
from uuid import UUID

import sendgrid
from sendgrid.helpers.mail import Content, From, HtmlContent, Mail, Subject, To

from app.config import settings
from app.database.models import Run

log = logging.getLogger(__name__)

# Deliberately permissive: this only guards against obviously malformed
# input at run-creation time. SendGrid itself validates deliverability.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_STATUS_VERBS = {
    "succeeded": "finished successfully",
    "failed": "failed",
    "timed_out": "timed out",
}


def is_valid_email(value: str) -> bool:
    """Return whether ``value`` looks like a plausible email address."""
    return bool(_EMAIL_RE.match(value))


def build_run_url(run_id: UUID) -> str | None:
    """Build a link to the run's results page, or None if unconfigured."""
    base_url = settings.hackbot_ui_base_url.rstrip("/")
    if not base_url:
        return None
    return f"{base_url}/runs/{run_id}"


@lru_cache(maxsize=1)
def _client() -> sendgrid.SendGridAPIClient:
    return sendgrid.SendGridAPIClient(api_key=settings.sendgrid_api_key)


def _status_verb(status: str) -> str:
    return _STATUS_VERBS.get(status, status)


def _build_lines(run: Run) -> list[str]:
    lines = [
        f"Agent: {run.agent}",
        f"Run ID: {run.run_id}",
        f"Status: {run.status}",
    ]
    if run.error:
        lines.append(f"Error: {run.error}")
    run_url = build_run_url(run.run_id)
    if run_url:
        lines.append(f"Results: {run_url}")
    return lines


def _build_message(run: Run) -> Mail:
    lines = _build_lines(run)
    plain_text = "\n".join(lines)
    html_text = "<br>".join(html.escape(line) for line in lines)

    return Mail(
        From(settings.notification_sender_email),
        To(run.notify_email),
        Subject(f"[hackbot] {run.agent} run {_status_verb(run.status)}"),
        Content("text/plain", plain_text),
        HtmlContent(f"<p>{html_text}</p>"),
    )


def _send_sync(run: Run) -> None:
    message = _build_message(run)
    response = _client().send(message=message)
    log.info(
        "Sent run-completion email for run %s to %s (status code %s)",
        run.run_id,
        run.notify_email,
        response.status_code,
    )


async def notify_run_complete(run: Run) -> None:
    """Best-effort email notification for a run that just went terminal."""
    if not settings.sendgrid_api_key:
        return
    if not run.notify_email:
        return
    if not settings.notification_sender_email:
        log.warning(
            "SENDGRID_API_KEY is set but NOTIFICATION_SENDER_EMAIL is not; "
            "skipping notification for run %s",
            run.run_id,
        )
        return

    try:
        await asyncio.to_thread(_send_sync, run)
    except Exception:
        log.exception("Failed to send completion email for run %s", run.run_id)
