"""Email-domain recordable action.

An agent -- or the deterministic code around it, via :func:`record_email` --
records a report it wants delivered by email; the apply side sends it with
SendGrid (see ``handlers/email_handler.py``).

Recording rather than sending gives a notification the same properties as every
other action -- visible in the UI before it lands, delivered at most once, and
never sent at all for a run that did not succeed.

The team address, the sender and the local-testing override live apply-side, so
an agent only names the individuals its result concerns.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Annotated

from agent_tools.registry import ToolError, tool, tools_in
from pydantic import Field

from hackbot_runtime.actions.recorder import ActionsRecorder

ACTION_TYPE = "email.send"

# Substituted with the run's patch when the mail is sent. The agent decides
# whether the body mentions the patch at all, and how it is framed; this only
# says where the text goes.
PATCH_PLACEHOLDER = "{patch}"


def _params(
    to: Iterable[str],
    subject: str,
    body_markdown: str,
    attach_patch: bool = False,
) -> dict:
    subject = subject.strip()
    body_markdown = body_markdown.strip()
    if not subject:
        raise ToolError("subject must not be blank")
    if not body_markdown:
        raise ToolError("body_markdown must not be blank")

    recipients: list[str] = []
    for address in to:
        address = address.strip()
        if address and address not in recipients:
            recipients.append(address)

    return {
        "to": recipients,
        "subject": subject,
        "body_markdown": body_markdown,
        "attach_patch": attach_patch,
    }


@tool
async def send(
    recorder: ActionsRecorder,
    to: Annotated[
        list[str],
        Field(
            description=(
                "Who the report concerns, as email addresses. May be empty for a "
                "report that concerns no individual; the team address is added "
                "when the mail is sent, so never list it here."
            )
        ),
    ],
    subject: Annotated[
        str,
        Field(
            description=(
                "Subject line. Lead with the verdict, so it reads in an inbox "
                "listing without opening the mail."
            )
        ),
    ],
    body_markdown: Annotated[
        str,
        Field(
            description=(
                "Body in Markdown: headings, lists, tables and fenced code all "
                "render. Link every identifier a recipient would otherwise have "
                "to look up."
            )
        ),
    ],
    reasoning: Annotated[
        str,
        Field(description="Why these recipients need to hear about it (audit log)."),
    ],
) -> str:
    """Record an intended email.

    Recorded into the run summary for human review -- does not send any mail.
    """
    recorder.record(
        ACTION_TYPE, _params(to, subject, body_markdown), reasoning=reasoning
    )
    return f"Recorded {ACTION_TYPE} (#{len(recorder.actions) - 1})."


def record_email(
    recorder: ActionsRecorder,
    *,
    to: Iterable[str] = (),
    subject: str,
    body_markdown: str,
    attach_patch: bool = False,
    ref: str | None = None,
) -> dict:
    """Record a report the agent was never asked to decide on.

    For a run whose outcome is always worth reporting: the wording is code, not a
    model turn. ``to`` may be empty for a report that concerns no individual; the
    handler still addresses the team.

    The run's patch can travel two ways, independently: inline, by putting
    :data:`PATCH_PLACEHOLDER` in the body, and as a file, with ``attach_patch``.
    Either way it is read once when the mail is sent, so the diff a recipient
    reads and the file they save cannot differ.
    """
    return recorder.record(
        ACTION_TYPE,
        _params(to, subject, body_markdown, attach_patch),
        ref=ref,
    )


def demote_headings(md: str, by: int = 2) -> str:
    """Shift ATX headings down ``by`` levels so agent prose nests under our own.

    Lines inside code fences (and ``#include`` and the like, which lack the
    required space after ``#``) are left untouched.
    """
    out = []
    in_fence = False
    for line in md.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            out.append(line)
            continue
        match = re.match(r"(#{1,6}) ", line) if not in_fence else None
        if match:
            level = min(len(match.group(1)) + by, 6)
            line = "#" * level + line[len(match.group(1)) :]
        out.append(line)
    return "\n".join(out)


TOOLS = tools_in(__name__)
