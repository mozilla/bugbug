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

The recorded body is sent as it stands, except that ``{patch}`` is substituted
with the run's patch artifact. That artifact is read here rather than baked in at
record time, so an inline diff and an attachment of it cannot drift apart. How the
patch is introduced -- headings, fencing, whether it appears at all -- is the
recording agent's to decide.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

from hackbot_runtime.actions.email import PATCH_PLACEHOLDER
from hackbot_runtime.actions.handlers.base import ActionResult, ApplyContext
from hackbot_runtime.changes import PATCH_ARTIFACT

log = logging.getLogger(__name__)

# A diff long enough to bury the rest of the mail is cut off; the attachment,
# when the caller asked for one, still carries every line.
_MAX_PATCH_LINES = 400


def _recipients(params: dict[str, Any]) -> list[str]:
    override = os.environ.get("NOTIFICATION_OVERRIDE_EMAIL", "").strip()
    if override:
        return [override]
    recipients = list(params.get("to") or [])
    team = os.environ.get("NOTIFICATION_TEAM_EMAIL", "").strip()
    if team and team not in recipients:
        recipients.append(team)
    return recipients


async def _patch(ctx: ApplyContext) -> bytes | None:
    """The run's patch, or None when it published none.

    Read once however many ways the mail carries it, so the diff a recipient reads
    is the file they save. A patch that never made it to storage costs the
    recipient the patch, not the whole notification.
    """
    try:
        return await ctx.download_artifact(PATCH_ARTIFACT)
    except Exception:
        log.exception("Could not read the patch of run %s", ctx.run_id)
        return None


def _truncated(patch: bytes) -> str:
    lines = patch.decode(errors="replace").splitlines()
    if len(lines) <= _MAX_PATCH_LINES:
        return "\n".join(lines)
    return "\n".join(
        lines[:_MAX_PATCH_LINES]
        + [f"... truncated to {_MAX_PATCH_LINES} of {len(lines)} lines"]
    )


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
        inline = PATCH_PLACEHOLDER in body_md
        attach = bool(params.get("attach_patch"))
        patch = await _patch(ctx) if inline or attach else None
        if inline:
            body_md = body_md.replace(
                PATCH_PLACEHOLDER,
                _truncated(patch) if patch else "(the patch could not be read)",
            )
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
        if attach and patch is not None:
            message.add_attachment(
                Attachment(
                    FileContent(base64.b64encode(patch).decode()),
                    FileName(PATCH_ARTIFACT.rsplit("/", 1)[-1]),
                    disposition=Disposition("attachment"),
                )
            )

        response = sendgrid.SendGridAPIClient(api_key=api_key).send(message=message)
        return ActionResult.ok(
            {"recipients": recipients, "status_code": response.status_code}
        )
