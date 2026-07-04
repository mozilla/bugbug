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
    """Record an intended Phabricator patch submission.

    Submits whatever the run's final source-tree changes turn out to be — the
    same diff the runtime already collects into ``changes/changes.patch`` once
    the agent finishes, not a separately-supplied file. There's no local patch
    path to give here because that diff isn't final until after this call
    returns (it's computed once from the checkout's end state).

    Recorded into the run summary for human review — does not submit to
    Phabricator. Set revision_id to update an existing revision with a new
    diff, or omit it (and provide a title) to create a new one.
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
