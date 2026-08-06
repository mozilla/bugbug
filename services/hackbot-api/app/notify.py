"""Tell a Slack channel what a finished run did.

Delivery is email to a Slack channel address (Slack's "Email to channel"), which is why
this is an email module and not a Slack API client: Slack renders an inbound message's
*subject* as the message title, so the one-line summary lives there and the links live
in the body.

Best-effort and gated on configuration, mirroring
`services/hackbot-pulse-listener/app/notify.py`: an unconfigured deployment logs and
moves on rather than failing the run-completion path. Nothing retries a failed send, and
a redelivered `run.completed` event may post a second line to the channel.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Run, RunAction
from app.schemas import RunActionOutcome, parse_confidence

log = logging.getLogger(__name__)

# Slack renders an inbound email's subject as the message title and truncates long ones.
MAX_SUBJECT_LENGTH = 150

# Both of these happen inline on the run-completion push request, so they are additive
# with each other and with the Bugzilla writes the apply step makes. Overrunning the
# subscription's ack deadline earns a concurrent redelivery, which the claims in
# `app/actions_applier.py` exist to survive.
_BUGZILLA_TIMEOUT_SECONDS = 10
_SENDGRID_TIMEOUT_SECONDS = 10

_OUTCOME_WORDING = {
    RunActionOutcome.posted: "Posted to Bugzilla.",
    RunActionOutcome.held: "Not posted — awaiting review in the hackbot UI.",
    RunActionOutcome.failed: (
        "Tried to post, but at least one action failed — see the run."
    ),
    RunActionOutcome.no_actions: "Nothing to post — the run recorded no actions.",
}


def _findings(run: Run) -> dict:
    return (run.summary or {}).get("findings") or {}


def _bug_id(run: Run) -> int | None:
    return (run.inputs or {}).get("bug_id")


async def _outcome(db: AsyncSession, run: Run) -> RunActionOutcome:
    """What became of the run's actions, read from the rows the applier left behind.

    `populate_existing` because these rows are in the session's identity map and the
    session doesn't expire on commit, so a plain select would hand back stale copies.
    """
    result = await db.execute(
        select(RunAction)
        .where(RunAction.run_id == run.run_id)
        .execution_options(populate_existing=True)
    )
    statuses = [row.status for row in result.scalars()]
    if not statuses:
        return RunActionOutcome.no_actions
    if any(status == "failed" for status in statuses):
        return RunActionOutcome.failed
    if all(status == "applied" for status in statuses):
        return RunActionOutcome.posted
    return RunActionOutcome.held


async def _bug_product_component(bug_id: object) -> str | None:
    """The bug's `"<Product> :: <Component>"`, or None if it couldn't be read.

    Read from Bugzilla rather than from the run: the agent doesn't report the component,
    and Bugzilla is authoritative anyway, so a bug moved between components mid-triage
    reaches the team that owns it now.

    Anonymous on purpose, rather than reusing the runtime's authenticated client: a
    restricted bug must come back unreadable so its summary stays out of the channel,
    and an API key would defeat that.
    """
    try:
        async with httpx.AsyncClient(timeout=_BUGZILLA_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{settings.bugzilla_url.rstrip('/')}/rest/bug/{bug_id}",
                params={"include_fields": "product,component"},
            )
        response.raise_for_status()
        bugs = response.json().get("bugs") or []
        product = bugs[0].get("product") if bugs else None
        component = bugs[0].get("component") if bugs else None
        if not (isinstance(product, str) and isinstance(component, str)):
            log.warning("Bugzilla gave no product/component for bug %s", bug_id)
            return None
        return f"{product} :: {component}"
    except Exception:
        log.exception("Could not read the component of bug %s", bug_id)
        return None


async def _recipient_for(run: Run) -> str | None:
    """The Slack channel address a run's result belongs in, or None to stay quiet.

    Routes on the bug's component so each team hears about its own bugs. A component
    with no channel — and a bug that couldn't be read at all, which is most likely a
    restricted one — says nothing, because posting one team's triage into another
    team's channel is worse than silence.
    """
    channels = settings.notification_slack_emails
    if not channels:
        return None

    bug_id = _bug_id(run)
    key = await _bug_product_component(bug_id) if bug_id else None
    if key is None:
        log.info("Could not route run %s to a channel; not notifying", run.run_id)
        return None

    # Logged where the decision is made and the address isn't: a Slack
    # email-to-channel address is a posting credential and stays out of the logs, but
    # which component routed is what an operator needs.
    if key not in channels:
        log.info("No channel configured for %s (run %s)", key, run.run_id)
        return None

    log.info("Routing run %s to the channel for %s", run.run_id, key)
    return channels[key]


def _bug_url(bug_id: object) -> str:
    return f"{settings.bugzilla_url.rstrip('/')}/show_bug.cgi?id={bug_id}"


def _run_url(run: Run) -> str | None:
    if not settings.hackbot_ui_url:
        return None
    return f"{settings.hackbot_ui_url.rstrip('/')}/runs/{run.run_id}"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _confidence_text(findings: dict) -> str:
    """The run's confidence for display, or "unknown".

    Never a passthrough: a multi-line value would let the model (or a bug comment
    injecting into it) append its own lines to the body and forge the outcome line.
    `parse_confidence` is shared with the applier, so the level that decides an
    unattended write is the level the channel is told about.
    """
    confidence = parse_confidence(findings.get("confidence"))
    return confidence.value if confidence else "unknown"


def build_notification(run: Run, outcome: RunActionOutcome) -> tuple[str, str]:
    """Render the (subject, body) for a finished run.

    Pure, so the wording is testable without touching SendGrid. Every field degrades to
    something still worth reading, because the agent's plan is parsed best-effort.
    """
    findings = _findings(run)
    bug_id = _bug_id(run)

    subject = f"[{run.agent}] " + (f"Bug {bug_id}" if bug_id else f"Run {run.run_id}")
    summary = findings.get("summary")
    if isinstance(summary, str) and summary.strip():
        subject += f" — {summary}"
    # Collapsed as a whole, not per-field: `run.agent` and the summary are both
    # interpolated, and Slack renders the subject as the message title.
    subject = _one_line(subject)
    if len(subject) > MAX_SUBJECT_LENGTH:
        subject = subject[: MAX_SUBJECT_LENGTH - 1].rstrip() + "…"

    lines = [
        _OUTCOME_WORDING.get(outcome, f"Outcome: {outcome.value}"),
        f"Confidence: {_confidence_text(findings)}",
    ]
    if bug_id:
        lines.append(f"Bug: {_bug_url(bug_id)}")
    run_url = _run_url(run)
    if run_url:
        lines.append(f"Run: {run_url}")

    return subject, "\n".join(lines)


async def notify_run_completed(db: AsyncSession, run: Run) -> None:
    """Send the run's one-line result to its team's Slack channel address."""
    if not (settings.sendgrid_api_key and settings.notification_sender):
        log.info("Notification not configured; skipping for run %s", run.run_id)
        return

    recipient = await _recipient_for(run)
    if recipient is None:
        return

    # Deferred rather than module-scope, as `hackbot-pulse-listener/app/notify.py` does,
    # to keep the SDK out of startup for deployments that never send.
    import sendgrid
    from sendgrid.helpers.mail import Content, From, Mail, Subject, To

    subject, body = build_notification(run, await _outcome(db, run))
    client = sendgrid.SendGridAPIClient(api_key=settings.sendgrid_api_key)
    # The SDK defaults to no timeout and `send` occupies an executor thread, so a hung
    # send would hold one forever and enough of them would stall every notification.
    client.client.timeout = _SENDGRID_TIMEOUT_SECONDS
    message = Mail(
        From(settings.notification_sender),
        To(recipient),
        Subject(subject),
        Content("text/plain", body),
    )

    response = await asyncio.to_thread(client.send, message=message)
    # Deliberately not the recipient: a Slack email-to-channel address is a posting
    # credential, and this log line is retained far more widely than the channel itself.
    log.info(
        "Notified the channel about run %s (status %s)",
        run.run_id,
        response.status_code,
    )
