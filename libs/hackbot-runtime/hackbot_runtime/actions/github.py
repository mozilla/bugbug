"""GitHub-domain recordable actions.

Mirrors ``actions/phabricator.py``: the handler records an intended change
(nothing is pushed to GitHub here) and returns a short confirmation string. See
hackbot-api's ``app/action_handlers/github_handler.py`` for the apply side,
which pushes a branch carrying the run's commits and opens a pull request.
"""

from typing import Annotated

from agent_tools.registry import tool, tools_in
from pydantic import Field

from hackbot_runtime.actions.recorder import ActionsRecorder, confirmation

SUBMIT_PATCH_ACTION_TYPE = "github.submit_patch"

# Actions that deliver the working directory's changes to GitHub, gating the
# commits artifact built in ``context.publish_changes``. The GitHub counterpart
# of ``phabricator.PATCH_ACTION_TYPES``: kept separate so a GitHub submission
# does not also build a Phabricator diff, and vice versa.
PATCH_ACTION_TYPES = frozenset({SUBMIT_PATCH_ACTION_TYPE})


@tool
async def submit_patch(
    recorder: ActionsRecorder,
    title: Annotated[
        str,
        Field(
            description=(
                "Title for the new pull request: a single line describing the "
                "fix, as you would write a commit message subject."
            )
        ),
    ],
    reasoning: Annotated[
        str, Field(description="Why you are submitting this patch (for audit log).")
    ],
    body: Annotated[
        str | None,
        Field(
            description=(
                "Pull request description (GitHub Markdown). Explain what the "
                "change does and how you verified it. Keep it concise and do not "
                "narrate the investigation."
            ),
        ),
    ] = None,
) -> str:
    """Submit your fix for review as a new GitHub pull request.

    This is how you deliver a code fix to a repository hosted on GitHub. The
    pull request is opened from a new branch against the repository's default
    branch, as a draft.

    You do not supply a patch file. Your final code changes in the working
    directory (and any commits you made) are pushed as the pull request's
    commits, so make and verify all your edits first, then call this once you
    are done. Calling it records the submission as a proposed action for review;
    nothing is sent to GitHub during the run.

    Later actions can reference its `url`.
    """
    params: dict = {"title": title, "body": body}
    action = recorder.record(SUBMIT_PATCH_ACTION_TYPE, params, reasoning=reasoning)
    return confirmation(action)


TOOLS = tools_in(__name__)
