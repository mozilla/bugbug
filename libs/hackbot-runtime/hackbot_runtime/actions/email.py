"""Email-domain recordable action.

Deterministic code records the report a finished run owes its recipients through
:func:`record_email`; the apply side delivers it with SendGrid (see
``handlers/email_handler.py``). There is no model-facing ``@tool`` here: who
receives mail is the agent code's decision, not a model turn.

Recording rather than sending gives a notification the same properties as every
other action -- visible in the UI before it lands, delivered at most once, and
never sent at all for a run that did not succeed.

The team address, the sender and the local-testing override live apply-side, so
an agent only names the individuals its result concerns.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from agent_tools.registry import ToolError

from hackbot_runtime.actions.recorder import ActionsRecorder

ACTION_TYPE = "email.send"

# An inline diff is context, not the deliverable: the full patch rides along as an
# attachment, so a long one is cut off rather than pushed into a scroll.
MAX_PATCH_LINES = 400


def record_email(
    recorder: ActionsRecorder,
    *,
    to: Iterable[str] = (),
    subject: str,
    body_markdown: str,
    attach_artifacts: Iterable[str] = (),
    ref: str | None = None,
) -> dict:
    """Record an intended email.

    ``to`` may be empty for a report that concerns no individual; the handler
    still addresses the team. ``attach_artifacts`` names run artifact keys (e.g.
    ``changes/changes.patch``) to attach, downloaded at apply time so the body
    stays small and a missing artifact cannot fail the send.
    """
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

    return recorder.record(
        ACTION_TYPE,
        {
            "to": recipients,
            "subject": subject,
            "body_markdown": body_markdown,
            "attach_artifacts": [key for key in attach_artifacts if key],
        },
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


def patch_block(patch: str, max_lines: int = MAX_PATCH_LINES) -> str:
    """A fenced diff of ``patch``, truncated to ``max_lines``."""
    lines = patch.splitlines()
    block = ["```diff", *lines[:max_lines], "```"]
    if len(lines) > max_lines:
        block.append(
            f"\n_Patch truncated to {max_lines} lines; "
            "see the attached changes.patch for the full diff._"
        )
    return "\n".join(block)
