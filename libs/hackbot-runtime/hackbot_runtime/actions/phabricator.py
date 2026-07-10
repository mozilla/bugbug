"""Phabricator-domain recordable actions.

Mirrors ``actions/bugzilla.py``'s shape: the handler records an intended
change (nothing is submitted to Phabricator here) and returns a short
confirmation string. See ``actions/handlers/phabricator_handler.py`` for the
apply side.
"""

from __future__ import annotations

from typing import Annotated, Any

from agent_tools.registry import ToolError, tool, tools_in
from pydantic import Field

from hackbot_runtime.actions.recorder import ActionsRecorder


def _confirm(recorder: ActionsRecorder, action_type: str) -> str:
    return f"Recorded {action_type} (#{len(recorder.actions) - 1})."


@tool
async def submit_patch(
    recorder: ActionsRecorder,
    bug_id: Annotated[int, Field(description="Bug this patch fixes.")],
    reasoning: Annotated[
        str, Field(description="Why you are submitting this patch (for audit log).")
    ],
    revision_id: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "An existing Phabricator revision to attach a new diff to. "
                "Omit to create a brand-new revision instead — never inferred "
                "automatically, so pass this explicitly whenever you intend "
                "to update rather than create."
            ),
        ),
    ] = None,
    reviewers: Annotated[
        list[str] | None,
        Field(default=None, description="Reviewers to request on the revision."),
    ] = None,
    title: Annotated[
        str | None,
        Field(
            default=None,
            description="Revision title. Required when creating a new revision.",
        ),
    ] = None,
    summary: Annotated[
        str | None,
        Field(default=None, description="Revision summary/description."),
    ] = None,
    ref: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Optional label for this action so a later action (e.g. a "
                "bugzilla.add_comment in the same run) can reference its "
                "result once applied, via {{actions.<ref>.url}} in that "
                "action's text."
            ),
        ),
    ] = None,
) -> str:
    """Submit your fix for review as a Phabricator revision.

    This is how you deliver a code fix. Do not attach the patch to a bug: a
    Phabricator revision is the correct destination for a fix, not a bug
    attachment.

    You do not supply a patch file. Your final code changes in the working
    directory are submitted as the revision's diff, so make and verify all your
    edits first, then call this once you are done. Calling it records the
    submission as a proposed action for review; it is not sent to Phabricator
    during the run.

    To create a new revision, pass a `title` (and ideally a `summary`). To add a
    new diff to an existing revision instead, pass that revision's `revision_id`.
    Set `ref` if you want to reference the new revision's URL from another action
    in the same run, written as `{{actions.<ref>.url}}` (for example, inside a
    bug comment).
    """
    if revision_id is None and not title:
        raise ToolError("title is required when creating a new revision")

    params: dict[str, Any] = {
        "bug_id": bug_id,
        "revision_id": revision_id,
        "reviewers": reviewers or [],
        "title": title,
        "summary": summary,
    }
    recorder.record(
        "phabricator.submit_patch",
        params,
        reasoning=reasoning,
        ref=ref,
    )
    return _confirm(recorder, "phabricator.submit_patch")


TOOLS = tools_in(__name__)
