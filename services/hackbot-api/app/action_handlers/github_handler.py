"""Apply-side GitHub actions: push the run's commits and open a pull request.

Pairs with the recording side in ``hackbot_runtime/actions/github.py`` and the
payload built agent-side in ``hackbot_runtime.changes.build_github_commits``.
This service has no checkout (or git binary), so the commits are recreated
through GitHub's Git Data API: a blob per changed file, a tree per commit on top
of its parent's, then the commit itself. Commits made this way are attributed to
their original author and committed (and signed) by the GitHub App.

The pull request goes to the repository the agent's source was checked out
from, which must have the GitHub App installed.
"""

import json
import logging
import re
from functools import lru_cache
from typing import Any

from githubkit import AppInstallationAuthStrategy, GitHub
from githubkit.exception import RequestFailed

from app.action_handlers.base import ActionResult, ApplyContext
from app.config import settings
from app.github import READ_WRITE_PERMISSIONS, GitHubApp

log = logging.getLogger(__name__)

_COMMITS_ARTIFACT_KEY = "changes/github_commits.json"

# The forms a ``[source].repo_url`` on GitHub takes.
_GITHUB_REPO_URL_RE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[A-Za-z0-9-]+)/(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?/?$"
)

_GITLINK_MODE = "160000"


@lru_cache(maxsize=1)
def _github_app() -> GitHubApp:
    if settings.github is None:
        raise RuntimeError("The GitHub App is not configured (GITHUB_* settings)")
    return GitHubApp(settings.github)


def parse_repo_url(repo_url: str) -> tuple[str, str] | None:
    """``(owner, repo)`` for a GitHub repository URL, else ``None``."""
    match = _GITHUB_REPO_URL_RE.match(repo_url.strip())
    if match is None:
        return None
    return match["owner"], match["repo"]


def branch_name(run_id: str) -> str:
    """The branch a run's pull request is opened from.

    One per run: every submission in a run carries the same commits (there is
    one changes artifact per run), so a second one, or a retry, reuses it.
    """
    return f"hackbot/{run_id}"


async def _installation_client(
    owner: str, repo: str
) -> GitHub[AppInstallationAuthStrategy]:
    """A client for ``owner/repo``, with a token narrowed to that repository."""
    github_app = _github_app()
    # githubkit's ``rest`` namespace only holds a weak reference to its client,
    # so the client must outlive the call.
    app_client = github_app.as_app()
    installation = await app_client.rest.apps.async_get_repo_installation(owner, repo)
    return github_app.as_installation(
        installation.json()["id"],
        permissions=READ_WRITE_PERMISSIONS,
        repositories=[repo],
    )


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

    The branch is pushed to the source repository itself and the pull request
    targets its default branch. Retrying after a partial failure reuses the
    branch and any pull request already open from it.
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

        repo_url = submission["repo_url"]
        parsed = parse_repo_url(repo_url)
        if parsed is None:
            return ActionResult.failed(
                f"The agent's source repository {repo_url!r} is not on GitHub"
            )
        if not submission["commits"]:
            return ActionResult.failed("This run has no commits to submit")
        owner, repo = parsed
        branch = branch_name(ctx.run_id)

        try:
            github = await _installation_client(owner, repo)
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
            return ActionResult.failed(str(exc))

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
