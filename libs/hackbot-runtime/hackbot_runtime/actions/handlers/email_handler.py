"""Apply-side email action: delivers a recorded report through SendGrid.

Configured entirely by environment, so an agent never carries an address it
does not choose per run:

``SENDGRID_API_KEY``/``NOTIFICATION_SENDER``
    Required; without both, nothing is sent.
``NOTIFICATION_TEAM_EMAIL``
    Copied on every email and used as ``Reply-To``, so feedback reaches the team
    and the team sees what the agents report.
``NOTIFICATION_OVERRIDE_EMAIL``
    Replaces every recipient with this one address. The single switch that keeps
    a development deployment from mailing real developers.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

from hackbot_runtime.actions.handlers.base import ActionResult, ApplyContext

log = logging.getLogger(__name__)


def _recipients(params: dict[str, Any]) -> list[str]:
    override = os.environ.get("NOTIFICATION_OVERRIDE_EMAIL", "").strip()
    if override:
        return [override]
    recipients = list(params.get("to") or [])
    team = os.environ.get("NOTIFICATION_TEAM_EMAIL", "").strip()
    if team and team not in recipients:
        recipients.append(team)
    return recipients


async def _attachments(params: dict[str, Any], ctx: ApplyContext) -> list[tuple]:
    """``(filename, bytes)`` for each recorded artifact key that downloads.

    A patch that never made it to storage costs the recipient an attachment, not
    the whole notification.
    """
    files = []
    for key in params.get("attach_artifacts") or []:
        try:
            files.append((key.rsplit("/", 1)[-1], await ctx.download_artifact(key)))
        except Exception:
            log.exception("Could not attach artifact %s of run %s", key, ctx.run_id)
    return files


class SendEmailHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        api_key = os.environ.get("SENDGRID_API_KEY", "")
        sender = os.environ.get("NOTIFICATION_SENDER", "")
        if not (api_key and sender):
            return ActionResult.failed(
                "SENDGRID_API_KEY / NOTIFICATION_SENDER are not configured"
            )

        recipients = _recipients(params)
        if not recipients:
            return ActionResult.failed("No recipients for this email")

        import markdown2
        import sendgrid
        from sendgrid.helpers.mail import (
            Attachment,
            Cc,
            Content,
            Disposition,
            FileContent,
            FileName,
            From,
            HtmlContent,
            Mail,
            ReplyTo,
            Subject,
            To,
        )

        body_md = params["body_markdown"]
        message = Mail(
            From(sender),
            [To(recipients[0])] + [Cc(address) for address in recipients[1:]],
            Subject(params["subject"]),
            Content("text/plain", body_md),
            HtmlContent(
                markdown2.markdown(body_md, extras=["fenced-code-blocks", "tables"])
            ),
        )
        team = os.environ.get("NOTIFICATION_TEAM_EMAIL", "").strip()
        if team:
            message.reply_to = ReplyTo(team)
        for filename, content in await _attachments(params, ctx):
            message.add_attachment(
                Attachment(
                    FileContent(base64.b64encode(content).decode()),
                    FileName(filename),
                    disposition=Disposition("attachment"),
                )
            )

        try:
            response = sendgrid.SendGridAPIClient(api_key=api_key).send(message=message)
        except Exception as exc:
            log.exception("Failed to email run %s to %s", ctx.run_id, recipients)
            return ActionResult.failed(str(exc))

        return ActionResult.ok(
            {"recipients": recipients, "status_code": response.status_code}
        )
