"""Bugzilla-domain recordable actions.

Each handler takes the ``ActionsRecorder`` as its first positional
parameter (excluded from the agent-facing schema) plus the agent-facing
args annotated with ``Annotated[T, Field(...)]`` so any adapter can derive
the JSON Schema from the signature. Handlers return a short confirmation
string and raise ``ActionInputError`` on invalid input.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from hackbot_runtime.actions.recorder import ActionsRecorder
from hackbot_runtime.actions.registry import ActionDefinition, ActionInputError

_COMMENT_FOOTER = (
    "*This is an automated analysis result. If this result is incorrect "
    "please add a needinfo and feel free to correct the error.* "
)
_ATTACHMENT_COMMENT_FOOTER = (
    "*This is the analysis tool's suggested fix. Feel welcome to adopt "
    "it as a starting point and evolve it as needed to meet our coding "
    "standards.*"
)


def _confirm(recorder: ActionsRecorder, action_type: str) -> str:
    return f"Recorded {action_type} (#{len(recorder.actions) - 1})."


async def update_bug(
    recorder: ActionsRecorder,
    bug_id: Annotated[int, Field(description="Bug ID to change.")],
    changes: Annotated[
        dict[str, Any],
        Field(
            description=(
                "Field → new value. Accepts any field the Bugzilla REST "
                "PUT endpoint accepts (status, resolution, priority, "
                "severity, whiteboard, assigned_to, component, product, "
                "version, ...). For list fields (keywords, cc, blocks, "
                "depends_on, see_also) you may pass "
                '{"add": [...], "remove": [...], "set": [...]}.'
            )
        ),
    ],
    reasoning: Annotated[
        str,
        Field(
            description=(
                "Why you are recording this change. Stored alongside the "
                "action so the human reviewer can audit."
            )
        ),
    ],
) -> str:
    recorder.record(
        "bugzilla.update_bug",
        {"bug_id": bug_id, "changes": changes},
        reasoning=reasoning,
    )
    return _confirm(recorder, "bugzilla.update_bug")


async def add_comment(
    recorder: ActionsRecorder,
    bug_id: Annotated[int, Field(description="Bug ID to comment on.")],
    text: Annotated[str, Field(description="Comment body (Markdown supported).")],
    reasoning: Annotated[
        str, Field(description="Why you are recording this comment (for audit log).")
    ],
    is_private: Annotated[
        bool,
        Field(
            default=False,
            description="Mark the comment private (security group only).",
        ),
    ] = False,
) -> str:
    text_with_footer = text.rstrip() + "\n\n" + _COMMENT_FOOTER
    recorder.record(
        "bugzilla.add_comment",
        {"bug_id": bug_id, "text": text_with_footer, "is_private": is_private},
        reasoning=reasoning,
    )
    return _confirm(recorder, "bugzilla.add_comment")


async def add_attachment(
    recorder: ActionsRecorder,
    bug_id: Annotated[int, Field(description="Bug ID to attach to.")],
    file_path: Annotated[
        str,
        Field(
            description=(
                "Local path to the file. The runtime uploads a copy as an "
                "artifact so the apply step can fetch it; the local path "
                "is not persisted."
            )
        ),
    ],
    reasoning: Annotated[
        str, Field(description="Why you are attaching this (for audit log).")
    ],
    summary: Annotated[
        str | None,
        Field(
            default=None,
            description="Short description of the attachment. Defaults to the filename.",
        ),
    ] = None,
    content_type: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "MIME type. Guessed from extension if omitted. Ignored "
                "when is_patch=true."
            ),
        ),
    ] = None,
    is_patch: Annotated[
        bool,
        Field(
            default=False,
            description=(
                "Mark as a patch (Bugzilla forces text/plain and enables diff view)."
            ),
        ),
    ] = False,
    comment: Annotated[
        str | None,
        Field(
            default=None,
            description="Optional comment to record alongside the attachment.",
        ),
    ] = None,
) -> str:
    if not os.path.isfile(file_path):
        raise ActionInputError(f"file not found: {file_path}")

    file_name = os.path.basename(file_path)
    resolved_summary = summary or file_name
    size = os.path.getsize(file_path)
    if is_patch:
        resolved_content_type = "text/plain"
    else:
        resolved_content_type = content_type or (
            mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        )

    params: dict[str, Any] = {
        "bug_id": bug_id,
        "file_name": file_name,
        "summary": resolved_summary,
        "content_type": resolved_content_type,
        "is_patch": is_patch,
        "size_bytes": size,
    }
    if comment:
        params["comment"] = comment.rstrip() + "\n\n" + _ATTACHMENT_COMMENT_FOOTER

    recorder.record(
        "bugzilla.add_attachment",
        params,
        reasoning=reasoning,
        attachments={"file": Path(file_path)},
    )
    return _confirm(recorder, "bugzilla.add_attachment")


async def create_bug(
    recorder: ActionsRecorder,
    product: Annotated[str, Field(description="Bugzilla product.")],
    component: Annotated[str, Field(description="Bugzilla component.")],
    summary: Annotated[str, Field(description="Bug summary (title).")],
    version: Annotated[str, Field(description="Product version affected.")],
    description: Annotated[str, Field(description="Comment 0. Markdown is enabled.")],
    reasoning: Annotated[
        str, Field(description="Why you are filing this bug (for audit log).")
    ],
    extra: Annotated[
        dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "Optional additional fields accepted by Bugzilla's POST /bug "
                "endpoint (severity, priority, keywords, whiteboard, blocks, "
                "depends_on, cc, groups, op_sys, platform, assigned_to, "
                "see_also, ...). Merged into the recorded body — explicit "
                "top-level args win on conflict."
            ),
        ),
    ] = None,
) -> str:
    body: dict[str, Any] = {
        "product": product,
        "component": component,
        "summary": summary,
        "version": version,
        "description": description,
        "is_markdown": True,
    }
    for k, v in (extra or {}).items():
        body.setdefault(k, v)

    recorder.record("bugzilla.create_bug", body, reasoning=reasoning)
    return _confirm(recorder, "bugzilla.create_bug")


DEFINITIONS: list[ActionDefinition] = [
    ActionDefinition(
        type="bugzilla.update_bug",
        description=(
            "Record an intended change to a Bugzilla bug. Recorded into the "
            "run summary for human review — does not modify Bugzilla."
        ),
        handler=update_bug,
    ),
    ActionDefinition(
        type="bugzilla.add_comment",
        description=(
            "Record an intended comment on a bug. Use is_private=true for "
            "security-sensitive notes. Recorded into the run summary for "
            "human review — does not post to Bugzilla."
        ),
        handler=add_comment,
    ),
    ActionDefinition(
        type="bugzilla.add_attachment",
        description=(
            "Record an intended file attachment on a bug. Pass a local "
            "filesystem path — the runtime uploads a copy of the file "
            "alongside summary.json so the apply step can fetch it. For "
            "patches, set is_patch=true and omit content_type. Recorded "
            "into the run summary for human review — does not upload to "
            "Bugzilla."
        ),
        handler=add_attachment,
    ),
    ActionDefinition(
        type="bugzilla.create_bug",
        description=(
            "Record an intended new-bug filing. The description becomes "
            "comment 0 and is rendered as Markdown. Recorded into the run "
            "summary for human review — does not file in Bugzilla."
        ),
        handler=create_bug,
    ),
]
