"""Phabricator-domain recordable actions.

Mirrors ``actions/bugzilla.py``'s shape: the handler records an intended
change (nothing is submitted to Phabricator here) and returns a short
confirmation string. See ``actions/handlers/phabricator_handler.py`` for the
apply side.

Creating a revision and updating one are deliberately two tools: each takes
only the parameters its own case needs, so the agent cannot create a revision
when it meant to update one (or invent a revision id) by getting one optional
argument wrong.
"""

from __future__ import annotations

from typing import Annotated

from agent_tools.registry import tool, tools_in
from pydantic import Field

from hackbot_runtime.actions.recorder import ActionsRecorder

# Both patch actions submit the working directory's changes as a diff, so
# anything gated on "this run submits a patch" — today the diff artifact built
# in ``context.publish_changes`` — has to cover both types.
PATCH_ACTION_TYPES = frozenset({"phabricator.submit_patch", "phabricator.update_patch"})


def _confirm(recorder: ActionsRecorder, action_type: str) -> str:
    return f"Recorded {action_type} (#{len(recorder.actions) - 1})."


@tool
async def submit_patch(
    recorder: ActionsRecorder,
    bug_id: Annotated[int, Field(description="Bug this patch fixes.")],
    title: Annotated[
        str,
        Field(
            description=(
                "Title for the new revision: a single line describing the fix, "
                "as you would write a commit message subject."
            )
        ),
    ],
    reasoning: Annotated[
        str, Field(description="Why you are submitting this patch (for audit log).")
    ],
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
    """Submit your fix for review as a new Phabricator revision.

    This is how you deliver a code fix. Do not attach the patch to a bug: a
    Phabricator revision is the correct destination for a fix, not a bug
    attachment. If the fix belongs on a revision that already exists (for
    example, you were asked for changes on it), use ``update_patch`` instead.

    You do not supply a patch file. Your final code changes in the working
    directory are submitted as the revision's diff, so make and verify all your
    edits first, then call this once you are done. Calling it records the
    submission as a proposed action for review; it is not sent to Phabricator
    during the run.

    Set `ref` if you want to reference the new revision's URL from another action
    in the same run, written as `{{actions.<ref>.url}}` (for example, inside a
    bug comment).
    """
    recorder.record(
        "phabricator.submit_patch",
        {"bug_id": bug_id, "title": title, "summary": summary},
        reasoning=reasoning,
        ref=ref,
    )
    return _confirm(recorder, "phabricator.submit_patch")


@tool
async def update_patch(
    recorder: ActionsRecorder,
    revision_id: Annotated[
        int,
        Field(
            description=(
                "The existing Phabricator revision to attach the new diff to, as "
                "a number (12345 for D12345). Only pass a revision id you were "
                "given or read from the bug: never guess one."
            )
        ),
    ],
    reasoning: Annotated[
        str, Field(description="Why you are updating this patch (for audit log).")
    ],
) -> str:
    """Submit your fix as a new diff on an existing Phabricator revision.

    Use this only when a revision for this work already exists (for example, you
    are addressing review comments on it) and you know its id. To deliver a fix
    that has no revision yet, use ``submit_patch`` instead. If the review only
    asks a question and no code change is warranted, use ``add_comment``.

    You do not supply a patch file. Your final code changes in the working
    directory are submitted as the new diff, so make and verify all your edits
    first, then call this once you are done. Calling it records the submission as
    a proposed action for review; it is not sent to Phabricator during the run.

    Only the diff changes: the revision keeps its title, summary, and bug
    association exactly as they are.
    """
    recorder.record(
        "phabricator.update_patch",
        {"revision_id": revision_id},
        reasoning=reasoning,
    )
    return _confirm(recorder, "phabricator.update_patch")


@tool
async def add_comment(
    recorder: ActionsRecorder,
    revision_id: Annotated[
        int, Field(description="Differential revision to comment on (the D number).")
    ],
    text: Annotated[str, Field(description="Comment body (Remarkup supported).")],
    reasoning: Annotated[
        str, Field(description="Why you are recording this comment (for audit log).")
    ],
) -> str:
    """Record an intended comment on a Phabricator revision.

    Use this to reply on a revision — for example, to answer a question — when
    no code change is required. This does not deliver a fix: to submit code
    changes, use ``submit_patch`` instead. Recorded into the run summary for
    human review; nothing is posted to Phabricator during the run.
    """
    recorder.record(
        "phabricator.add_comment",
        {"revision_id": revision_id, "text": text.rstrip()},
        reasoning=reasoning,
    )
    return _confirm(recorder, "phabricator.add_comment")


TOOLS = tools_in(__name__)
