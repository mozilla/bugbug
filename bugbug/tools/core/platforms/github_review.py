"""GitHub review publication, reusable outside the review service or LangChain."""

from collections.abc import Awaitable, Callable
from logging import getLogger

import httpx
from unidiff import PatchSet

from bugbug.tools.core.platforms.github import GitHubAPI, GitHubSnapshotChanged

logger = getLogger(__name__)


class GitHubReviewRejected(ValueError):
    """GitHub rejected a review's comment coordinates or payload."""


class GitHubReviewPublisher:
    """Publish one PR review, recovering the result of an uncertain write on retry."""

    def __init__(self, api: GitHubAPI, bot_login: str):
        self.api = api
        self.bot_login = bot_login

    async def publish(
        self,
        *,
        repo: str,
        number: int,
        sha: str,
        marker: str,
        body: str,
        comments: list[dict],
        is_current: Callable[[], Awaitable[bool]],
    ) -> list[dict]:
        prefix = f"repos/{repo}/pulls/{number}"
        existing = None
        page = 1
        while True:
            reviews = (
                await self.api.request(
                    "GET", f"{prefix}/reviews", params={"per_page": 100, "page": page}
                )
            ).json()
            existing = next(
                (
                    r
                    for r in reviews
                    if marker in (r.get("body") or "")
                    and r["user"]["login"] == self.bot_login
                    and r["commit_id"] == sha
                ),
                None,
            )
            if existing or len(reviews) < 100:
                break
            page += 1
        if existing is None:
            if not await is_current():
                raise GitHubSnapshotChanged(
                    "PR or enrollment changed before publishing"
                )
            diff = (
                await self.api.request(
                    "GET", prefix, headers={"Accept": "application/vnd.github.diff"}
                )
            ).text
            allowed = {
                file.path: {
                    line.target_line_no
                    for hunk in file
                    for line in hunk
                    if line.is_added
                }
                for file in PatchSet.from_string(diff)
            }
            anchored = []
            for comment in comments:
                if (
                    comment.get("side") != "RIGHT"
                    or comment.get("start_line", comment["line"]) != comment["line"]
                    or comment["line"] not in allowed.get(comment["path"], set())
                ):
                    logger.warning(
                        "Skipping comment outside the final added lines: %s:%s",
                        comment["path"],
                        comment["line"],
                    )
                    continue
                anchored.append(comment)
            if not await is_current():
                raise GitHubSnapshotChanged("PR changed while preparing publication")
            if not anchored:
                body += "\n\nNo inline findings."
            try:
                existing = (
                    await self.api.request(
                        "POST",
                        f"{prefix}/reviews",
                        json={
                            "commit_id": sha,
                            "event": "COMMENT",
                            "body": f"{marker}\n{body}",
                            "comments": anchored,
                        },
                    )
                ).json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 422:
                    raise GitHubReviewRejected(
                        "GitHub rejected the review coordinates"
                    ) from e
                raise
        published = []
        page = 1
        while True:
            batch = (
                await self.api.request(
                    "GET",
                    f"{prefix}/reviews/{existing['id']}/comments",
                    params={"per_page": 100, "page": page},
                )
            ).json()
            published.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return published
