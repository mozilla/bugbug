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

import asyncio
import logging
from pathlib import Path
from string import Template

import sendgrid
from sendgrid.helpers.mail import From, HtmlContent, Mail, Subject, To

from app.config import settings
from app.database.models import Run

log = logging.getLogger(__name__)

# A stalled SendGrid call must not hold finalization open.
_SEND_TIMEOUT_SECONDS = 10

_TEMPLATES = Path(__file__).parent / "templates"
_HTML_TEMPLATE = Template((_TEMPLATES / "run_completed.html").read_text())


def run_url(run_id: str) -> str:
    return f"{settings.hackbot_ui_url.rstrip('/')}/runs/{run_id}"


def build_message(run: Run) -> tuple[str, str]:
    """Compose the subject and HTML body of the notice for ``run``."""
    outcome = run.status.replace("_", " ")
    label = f"{run.agent} run {str(run.run_id)[:8]}"
    bug_id = run.inputs.get("bug_id")
    if bug_id is not None:
        label += f" for bug {bug_id}"
    subject = f"[Hackbot] {label} {outcome}"

    values = {"label": label, "outcome": outcome, "url": run_url(str(run.run_id))}
    html_body = _HTML_TEMPLATE.substitute(values)
    return subject, html_body


def _send_sync(recipient: str, subject: str, html_body: str) -> int:
    message = Mail(
        From(settings.notification_sender),
        To(recipient),
        Subject(subject),
        html_content=HtmlContent(html_body),
    )
    client = sendgrid.SendGridAPIClient(api_key=settings.sendgrid_api_key)
    # The SendGrid wrapper has no timeout option; its HTTP client does.
    client.client.timeout = _SEND_TIMEOUT_SECONDS
    response = client.send(message=message)
    return response.status_code


async def notify_requester(run: Run) -> bool:
    """Mail the run's requester about its terminal state. Returns whether it sent."""
    recipient = run.requested_by
    subject, html_body = build_message(run)
    try:
        status_code = await asyncio.to_thread(_send_sync, recipient, subject, html_body)
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
