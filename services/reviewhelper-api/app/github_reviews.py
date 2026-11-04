"""Shared GitHub enrollment, request and publication eligibility checks."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import ReviewRequest
from app.enums import Platform, ReviewStatus
from app.github import GitHubClient, get_github_app
from app.tasks import create_review_task


@dataclass(frozen=True)
class GitHubTarget:
    repo: str
    number: int
    sha: str

    @classmethod
    def from_request(cls, review: ReviewRequest):
        owner, repo = review.owner, review.repo
        number, sha = review.pr_number, review.sha
        if owner is None or repo is None or number is None or sha is None:
            raise ValueError("GitHub review request has an incomplete target")
        return cls(f"{owner}/{repo}", number, sha)


def is_enrolled(repo: str) -> bool:
    # GitHub owner and repository names are case-insensitive.
    return repo.lower() in {
        enrolled.lower() for enrolled in settings.github_repositories
    }


async def request_github_review(
    db: AsyncSession,
    api: GitHubClient,
    repo: str,
    pr_number: int,
    user_id: int,
    user_name: str,
) -> ReviewRequest:
    pr = (await api.request("GET", f"repos/{repo}/pulls/{pr_number}")).json()
    if pr["state"] != "open" or pr["draft"]:
        raise HTTPException(409, "Only open, non-draft pull requests can be reviewed")
    # Store the canonical name so differently cased requests share a snapshot.
    owner, name = pr["base"]["repo"]["full_name"].split("/", 1)
    values = dict(
        platform=Platform.GITHUB,
        owner=owner,
        repo=name,
        pr_number=pr_number,
        sha=pr["head"]["sha"],
        user_id=user_id,
        user_name=user_name,
        status=ReviewStatus.PENDING,
    )
    stmt = (
        insert(ReviewRequest)
        .values(**values)
        .on_conflict_do_nothing()
        .returning(ReviewRequest.id)
    )
    request_id = await db.scalar(stmt)
    if request_id is None:
        review = await db.scalar(
            select(ReviewRequest).where(
                ReviewRequest.platform == Platform.GITHUB,
                ReviewRequest.owner == owner,
                ReviewRequest.repo == name,
                ReviewRequest.pr_number == pr_number,
                ReviewRequest.sha == pr["head"]["sha"],
            )
        )
    else:
        review = await db.get(ReviewRequest, request_id)
    if review is None:
        raise RuntimeError("Could not load the persisted GitHub review request")
    await db.commit()
    # Retried delivery also queues an existing pending row, repairing enqueue failure.
    if review.status == ReviewStatus.PENDING:
        delay = timedelta(seconds=settings.github_review_delay_seconds)
        await create_review_task(review.id, datetime.now(UTC) + delay, deduplicate=True)
    return review


async def current_pr(review: ReviewRequest) -> dict | None:
    """Return an eligible PR only if enrollment and its immutable snapshot still match."""
    target = GitHubTarget.from_request(review)
    if not is_enrolled(target.repo):
        return None
    try:
        api = await get_github_app().repo_client(target.repo)
        pr = (
            await api.request("GET", f"repos/{target.repo}/pulls/{target.number}")
        ).json()
    except httpx.HTTPStatusError as e:
        # The App was uninstalled, lost access to the repository, or the PR is gone.
        if e.response.status_code == 404:
            return None
        raise
    if pr["state"] != "open" or pr["draft"] or pr["head"]["sha"] != review.sha:
        return None
    return pr
