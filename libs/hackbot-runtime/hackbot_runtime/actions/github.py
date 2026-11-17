"""GitHub-domain recordable actions.

Mirrors ``actions/phabricator.py``'s shape: the tools record an intended comment
(nothing is posted to GitHub here) and return a short confirmation string. See
hackbot-api's ``app/action_handlers/github_handler.py`` for the apply side.
"""

from typing import Annotated, Any

from agent_tools.registry import ToolError, tool, tools_in
from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from hackbot_runtime.actions.recorder import ActionsRecorder, confirmation

ADD_COMMENT_ACTION_TYPE = "github.add_comment"
ADD_INLINE_COMMENTS_ACTION_TYPE = "github.add_inline_comments"

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class InlineComment(BaseModel):
    path: Annotated[
        _Text, Field(description="Path of the file in the repository, e.g. src/a.py.")
    ]
    line: Annotated[
        int,
        Field(
            ge=1,
            description=(
                "Line in the new version of the file. It must appear in the pull "
                "request's diff, as a changed line or the context around one."
            ),
        ),
    ]
    body: Annotated[_Text, Field(description="Comment body (Markdown supported).")]
    start_line: Annotated[
        int | None,
        Field(
            ge=1,
            description="First line of a multi-line comment ending at `line`.",
        ),
    ] = None

    @model_validator(mode="after")
    def _start_line_before_line(self) -> InlineComment:
        if self.start_line is not None and self.start_line >= self.line:
            raise ValueError("start_line must be before line")
        return self


# The tool adapters pass the agent's arguments through unvalidated, so these
# models are the enforcement; the tool signatures below are the agent's schema.
class _PullRequest(BaseModel):
    # GitHub's own rules for owner and repository names.
    repo: Annotated[str, Field(pattern=r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")]
    pull_number: Annotated[int, Field(ge=1)]


class _Comment(_PullRequest):
    text: _Text


class _InlineComments(_PullRequest):
    comments: Annotated[list[InlineComment], Field(min_length=1)]
    summary: _Text | None = None


def _validated(model: type[BaseModel], **values: Any) -> dict[str, Any]:
    try:
        validated = model.model_validate(values)
    except ValidationError as exc:
        raise ToolError(
            "invalid GitHub action",
            payload={
                "error": "invalid GitHub action",
                "details": exc.errors(include_url=False),
            },
        ) from exc
    return validated.model_dump(mode="json", exclude_none=True)


_Repo = Annotated[
    str,
    Field(description='The repository, as "owner/name" (e.g. mozilla/bugbug).'),
]
_PullNumber = Annotated[int, Field(description="The pull request number.")]
_Reasoning = Annotated[
    str, Field(description="Why you are recording this comment (for audit log).")
]


@tool
async def add_comment(
    recorder: ActionsRecorder,
    repo: _Repo,
    pull_number: _PullNumber,
    text: Annotated[str, Field(description="Comment body (Markdown supported).")],
    reasoning: _Reasoning,
) -> str:
    """Record an intended comment on a GitHub pull request's conversation.

    Use this to reply on a pull request, for example to answer a question, or for
    feedback that is not about particular lines; for those, use
    ``add_inline_comments``. Recorded into the run summary for human review;
    nothing is posted to GitHub during the run.
    """
    params = _validated(_Comment, repo=repo, pull_number=pull_number, text=text)
    action = recorder.record(ADD_COMMENT_ACTION_TYPE, params, reasoning=reasoning)
    return confirmation(action)


@tool
async def add_inline_comments(
    recorder: ActionsRecorder,
    repo: _Repo,
    pull_number: _PullNumber,
    comments: Annotated[
        list[InlineComment],
        Field(min_length=1, description="The line comments, at least one."),
    ],
    reasoning: _Reasoning,
    summary: Annotated[
        str | None,
        Field(description="Optional overall comment shown above the line comments."),
    ] = None,
) -> str:
    """Record an intended review of a GitHub pull request: comments on its lines.

    The comments are posted together as one review, so pass every line comment
    for the pull request in a single call; each call becomes a separate review.
    Recorded into the run summary for human review; nothing is posted to GitHub
    during the run.
    """
    params = _validated(
        _InlineComments,
        repo=repo,
        pull_number=pull_number,
        comments=comments,
        summary=summary,
    )
    action = recorder.record(
        ADD_INLINE_COMMENTS_ACTION_TYPE, params, reasoning=reasoning
    )
    return confirmation(action)


TOOLS = tools_in(__name__)
