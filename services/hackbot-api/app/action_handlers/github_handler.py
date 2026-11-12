"""Apply-side GitHub actions: comment on pull requests as the hackbot GitHub App.

Pairs with the recording side in ``hackbot_runtime/actions/github.py``. Each
action gets an installation token for its own repository only, with nothing
but ``pull_requests: write``: enough to comment on a pull request, not to touch
its code (or to comment on a plain issue, which needs ``issues: write``).
"""

import logging
from functools import lru_cache
from types import MappingProxyType
from typing import Any

from githubkit import GitHub
from githubkit.exception import RequestFailed

from app.action_handlers.base import ActionResult, ApplyContext
from app.config import settings
from app.github import GitHubApp

log = logging.getLogger(__name__)

_COMMENT_PERMISSIONS = MappingProxyType({"pull_requests": "write"})


def plan_review_groups(actions: list[tuple[str, dict[str, Any]]]) -> list[list[int]]:
    """Group same-PR actions only when both general and inline comments exist."""
    by_pr: dict[tuple[str, int], list[int]] = {}
    for idx, (action_type, params) in enumerate(actions):
        if action_type in {"github.add_comment", "github.add_inline_comments"}:
            repo, number = params.get("repo"), params.get("pull_number")
            if not isinstance(repo, str) or not isinstance(number, int):
                continue
            key = (repo, number)
            by_pr.setdefault(key, []).append(idx)
    return [
        indices
        for indices in by_pr.values()
        if len({actions[i][0] for i in indices}) == 2
    ]


def merge_review(entries: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Preserve comment order, using general comments as review summary text."""
    first = entries[0][1]
    comments = []
    summaries = []
    for action_type, params in entries:
        if action_type == "github.add_comment":
            summaries.append(params["text"])
        else:
            comments.extend(params["comments"])
            if summary := params.get("summary"):
                summaries.append(summary)
    return {
        "repo": first["repo"],
        "pull_number": first["pull_number"],
        "comments": comments,
        "summary": "\n\n".join(summaries),
    }


class GitHubAppNotConfiguredError(Exception):
    pass


@lru_cache(maxsize=1)
def _app() -> GitHubApp:
    if settings.github is None:
        raise GitHubAppNotConfiguredError(
            "The GitHub App is not configured (GITHUB_APP_ID, GITHUB_PRIVATE_KEY)"
        )
    return GitHubApp(settings.github)


async def _client_for(owner: str, repo: str) -> GitHub:
    app = _app()
    # githubkit's ``rest`` namespace only holds a weak reference to its client.
    github = app.as_app()
    installation = await github.rest.apps.async_get_repo_installation(owner, repo)
    return app.as_installation(
        installation.json()["id"],
        permissions=_COMMENT_PERMISSIONS,
        repositories=[repo],
    )


def _describe(exc: Exception) -> str:
    """GitHub's own explanation of a failed request, which ``str()`` leaves out."""
    if not isinstance(exc, RequestFailed):
        return str(exc)
    response = exc.response
    try:
        payload = response.json()
    except ValueError:
        return f"GitHub API error {response.status_code}"
    detail = payload.get("message", "")
    if errors := payload.get("errors"):
        detail += f" ({'; '.join(map(str, errors))})"
    return f"GitHub API error {response.status_code}: {detail}"


class AddCommentHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        repo, number = params["repo"], params["pull_number"]
        owner, name = repo.split("/")
        try:
            github = await _client_for(owner, name)
            # PR conversation comments go through the issues API.
            response = await github.rest.issues.async_create_comment(
                owner, name, number, body=params["text"]
            )
        except Exception as exc:
            log.exception("Failed to comment on %s#%s", repo, number)
            return ActionResult.failed(_describe(exc))
        return ActionResult.ok(
            {
                "repo": repo,
                "pull_number": number,
                "comment_url": response.json()["html_url"],
            }
        )


class AddInlineCommentsHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        repo, number = params["repo"], params["pull_number"]
        owner, name = repo.split("/")
        review: dict[str, Any] = {
            "event": "COMMENT",
            "comments": [_review_comment(c) for c in params["comments"]],
        }
        if summary := params.get("summary"):
            review["body"] = summary
        try:
            github = await _client_for(owner, name)
            response = await github.rest.pulls.async_create_review(
                owner, name, number, data=review
            )
        except Exception as exc:
            log.exception("Failed to review %s#%s", repo, number)
            return ActionResult.failed(_describe(exc))
        return ActionResult.ok(
            {
                "repo": repo,
                "pull_number": number,
                "review_url": response.json()["html_url"],
            }
        )


def _review_comment(comment: dict[str, Any]) -> dict[str, Any]:
    # Always the new version of the file ("RIGHT"), which is what the agent
    # has checked out.
    review_comment = {
        "path": comment["path"],
        "line": comment["line"],
        "side": "RIGHT",
        "body": comment["body"],
    }
    if start_line := comment.get("start_line"):
        review_comment |= {"start_line": start_line, "start_side": "RIGHT"}
    return review_comment
