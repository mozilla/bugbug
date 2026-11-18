"""Apply-side GitHub actions, carried out as the hackbot GitHub App.

Pairs with the recording side in ``hackbot_runtime/actions/github.py``. Each
action gets an installation token for its own repository only, with the least
permissions it needs.

Comments on pull requests need nothing but ``pull_requests: write``: enough to
comment on a pull request, not to touch its code (or to comment on a plain
issue, which needs ``issues: write``).

``github.submit_patch`` pushes the run's commits and opens a pull request, from
the payload built agent-side in ``hackbot_runtime.changes.build_github_commits``.
This service has no checkout (or git binary), so the commits are recreated
through GitHub's Git Data API: a blob per changed file, a tree per commit on top
of its parent's, then the commit itself. Commits made this way are attributed to
their original author and committed (and signed) by the GitHub App. The artifact
is written by the agent, so nothing in it decides where hackbot writes. The pull
request goes to the repository set as the agent's ``github_repository`` in
``AGENT_REGISTRY``, which must have the GitHub App installed. A submission whose
artifact names another repository, or that changes workflow files, is refused
before any credentials are minted.
"""

import json
import logging
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from types import MappingProxyType
from typing import Any

from githubkit import GitHub
from githubkit.exception import RequestFailed

from app.action_handlers.base import ActionResult, ApplyContext
from app.agents import AGENT_REGISTRY
from app.config import settings
from app.github import READ_WRITE_PERMISSIONS, GitHubApp

log = logging.getLogger(__name__)

_COMMENT_PERMISSIONS = MappingProxyType({"pull_requests": "write"})


def plan_review_groups(actions: list[tuple[str, dict[str, Any]]]) -> list[list[int]]:
    """Group same-PR actions only when both general and inline comments exist."""
    by_pr: dict[tuple[str, int], list[int]] = {}
    for idx, (action_type, params) in enumerate(actions):
        if action_type in {"github.add_comment", "github.add_inline_comments"}:
            # Both are validated when the action is recorded.
            key = (params["repo"], params["pull_number"])
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


def review_member_result(action_type: str, result: dict[str, Any]) -> dict[str, Any]:
    """The result each action in a combined review keeps.

    A general comment keeps the shape of its own action's result, with the
    review as its ``comment_url``, so references to it still resolve.
    """
    if action_type != "github.add_comment":
        return result
    return {
        "repo": result["repo"],
        "pull_number": result["pull_number"],
        "comment_url": result["review_url"],
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


async def _client_for(
    owner: str, repo: str, permissions: Mapping[str, str] = _COMMENT_PERMISSIONS
) -> GitHub:
    """A client for ``owner/repo``, with a token narrowed to that repository."""
    app = _app()
    # githubkit's ``rest`` namespace only holds a weak reference to its client.
    github = app.as_app()
    installation = await github.rest.apps.async_get_repo_installation(owner, repo)
    return app.as_installation(
        installation.json()["id"],
        permissions=permissions,
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


# --- github.submit_patch ---------------------------------------------------

_COMMITS_ARTIFACT_KEY = "changes/github_commits.json"

# The forms a ``[source].repo_url`` on GitHub takes.
_GITHUB_REPO_URL_RE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[A-Za-z0-9-]+)/(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?/?$"
)

_GITLINK_MODE = "160000"

# GitHub only lets a token with `workflows: write` push changes here. hackbot
# never asks for it: the branch is pushed to the repository itself, not a fork,
# so its pull request's workflows run with that repository's secrets.
_WORKFLOWS_DIR = ".github/workflows"


def parse_repo_url(repo_url: str) -> tuple[str, str] | None:
    """``(owner, repo)`` for a GitHub repository URL, else ``None``."""
    match = _GITHUB_REPO_URL_RE.match(repo_url.strip())
    if match is None:
        return None
    return match["owner"], match["repo"]


def _workflow_paths(commits: Iterable[dict[str, Any]]) -> list[str]:
    """The paths under ``.github/workflows`` that ``commits`` change, sorted."""
    return sorted(
        {
            change["path"]
            for commit in commits
            for change in commit["changes"]
            if change["path"] == _WORKFLOWS_DIR
            or change["path"].startswith(f"{_WORKFLOWS_DIR}/")
        }
    )


def branch_name(run_id: str) -> str:
    """The branch a run's pull request is opened from.

    One per run: every submission in a run carries the same commits (there is
    one changes artifact per run), so a second one, or a retry, reuses it.
    """
    return f"hackbot/{run_id}"


async def _tree_entry(
    github: GitHub, owner: str, repo: str, change: dict[str, Any]
) -> dict[str, Any]:
    """The Git Data API tree entry for one changed path."""
    mode = change["mode"]
    entry_type = "commit" if mode == _GITLINK_MODE else "blob"
    if change.get("deleted"):
        sha = None
    elif "sha" in change:
        sha = change["sha"]
    else:
        blob = await github.rest.git.async_create_blob(
            owner, repo, data={"content": change["content"], "encoding": "base64"}
        )
        sha = blob.json()["sha"]
    return {"path": change["path"], "mode": mode, "type": entry_type, "sha": sha}


async def _push_commits(
    github: GitHub, owner: str, repo: str, submission: dict[str, Any]
) -> str:
    """Recreate the run's commits on top of its base; return the new head sha.

    Requests are made one at a time, as GitHub asks of content-creating calls to
    stay clear of its secondary rate limits.
    """
    parent = submission["base_commit"]
    base_commit = await github.rest.git.async_get_commit(owner, repo, parent)
    tree = base_commit.json()["tree"]["sha"]

    for commit in submission["commits"]:
        entries = [
            await _tree_entry(github, owner, repo, change)
            for change in commit["changes"]
        ]
        if entries:
            new_tree = await github.rest.git.async_create_tree(
                owner, repo, data={"base_tree": tree, "tree": entries}
            )
            tree = new_tree.json()["sha"]
        new_commit = await github.rest.git.async_create_commit(
            owner,
            repo,
            data={
                "message": commit["message"],
                "tree": tree,
                "parents": [parent],
                "author": commit["author"],
            },
        )
        parent = new_commit.json()["sha"]
    return parent


async def _point_branch(
    github: GitHub, owner: str, repo: str, branch: str, sha: str
) -> None:
    """Create ``branch`` at ``sha``, or move it there if it already exists.

    It only exists already if an earlier attempt at this run's submission got
    this far, since the name is the run's own.
    """
    try:
        await github.rest.git.async_create_ref(
            owner, repo, data={"ref": f"refs/heads/{branch}", "sha": sha}
        )
    except RequestFailed as exc:
        if exc.response.status_code != 422:
            raise
        await github.rest.git.async_update_ref(
            owner, repo, f"heads/{branch}", data={"sha": sha, "force": True}
        )


async def _open_pull_request(
    github: GitHub,
    owner: str,
    repo: str,
    *,
    branch: str,
    base: str,
    title: str,
    body: str | None,
) -> dict[str, Any]:
    """Open a draft pull request from ``branch``, or return the one already open."""
    data: dict[str, Any] = {
        "title": title,
        "head": branch,
        "base": base,
        # Everything hackbot submits is a draft, as on Phabricator, so a human
        # decides when it is ready for review.
        "draft": True,
    }
    if body:
        data["body"] = body
    try:
        response = await github.rest.pulls.async_create(owner, repo, data=data)
    except RequestFailed as exc:
        if exc.response.status_code != 422:
            raise
        existing = await github.rest.pulls.async_list(
            owner, repo, state="open", head=f"{owner}:{branch}"
        )
        pull_requests = existing.json()
        if not pull_requests:
            raise
        return pull_requests[0]
    return response.json()


class SubmitPatchHandler:
    """Applies ``github.submit_patch``: the run's commits become a pull request.

    The branch is pushed to the agent's configured ``github_repository`` itself,
    not a fork, and the pull request targets its default branch. Retrying after
    a partial failure reuses the branch and any pull request already open from
    it.
    """

    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        try:
            raw = await ctx.download_artifact(_COMMITS_ARTIFACT_KEY)
            submission = json.loads(raw)
        except Exception as exc:
            log.exception(
                "Failed to load GitHub commits artifact for run %s", ctx.run_id
            )
            return ActionResult.failed(
                f"Could not load the GitHub commits artifact for this run: {exc}"
            )

        # The target comes from trusted configuration. The artifact's repo_url
        # only has to agree with it, since the commits were built on that
        # checkout.
        spec = AGENT_REGISTRY.get(ctx.agent)
        configured = spec.github_repository if spec else None
        if not configured:
            return ActionResult.failed(
                f"Agent {ctx.agent!r} is not configured to open pull requests "
                "(no github_repository in its AGENT_REGISTRY entry)"
            )
        owner, _, repo = configured.partition("/")

        repo_url = submission["repo_url"]
        parsed = parse_repo_url(repo_url)
        if parsed is None:
            return ActionResult.failed(
                f"The agent's source repository {repo_url!r} is not on GitHub"
            )
        # GitHub owner and repository names are case-insensitive.
        if "/".join(parsed).lower() != configured.lower():
            return ActionResult.failed(
                f"The run's commits are for {repo_url!r}, but agent {ctx.agent!r} "
                f"may only open pull requests on {configured}"
            )
        if not submission["commits"]:
            return ActionResult.failed("This run has no commits to submit")
        workflow_paths = _workflow_paths(submission["commits"])
        if workflow_paths:
            return ActionResult.failed(
                f"The run changes files under {_WORKFLOWS_DIR}/, which hackbot does "
                f"not submit: {', '.join(workflow_paths)}"
            )
        branch = branch_name(ctx.run_id)

        try:
            github = await _client_for(owner, repo, READ_WRITE_PERMISSIONS)
            repository = await github.rest.repos.async_get(owner, repo)
            default_branch = repository.json()["default_branch"]
            head = await _push_commits(github, owner, repo, submission)
            await _point_branch(github, owner, repo, branch, head)
            pull_request = await _open_pull_request(
                github,
                owner,
                repo,
                branch=branch,
                base=default_branch,
                title=params["title"].strip(),
                body=params.get("body"),
            )
        except Exception as exc:
            log.exception(
                "Failed to open a pull request on %s/%s for run %s",
                owner,
                repo,
                ctx.run_id,
            )
            return ActionResult.failed(_describe(exc))

        return ActionResult.ok(
            {
                "pull_request_number": pull_request["number"],
                # `url` is the field a `{{actions.<action_id>.url}}` placeholder
                # reads, so it is the page a human would want.
                "url": pull_request["html_url"],
                "repository": f"{owner}/{repo}",
                "branch": branch,
            }
        )
