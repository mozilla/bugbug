"""GitHub App webhook ingress and back-office review requests."""

import hashlib
import hmac
import json
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_external_api_key
from app.config import settings
from app.database.connection import get_db
from app.github import get_github_app
from app.github_reviews import is_enrolled, request_github_review

router = APIRouter(tags=["github"])
DB = Annotated[AsyncSession, Depends(get_db)]


class GitHubReviewRequest(BaseModel):
    owner: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]*$")
    repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    pr_number: int = Field(gt=0)
    user_id: int = Field(gt=0)
    user_name: str = Field(min_length=1)


@router.post("/request/github", dependencies=[Depends(verify_external_api_key)])
async def manual_request(body: GitHubReviewRequest, db: DB):
    """Trusted back-office verifies user authorization before calling this endpoint."""
    repo = f"{body.owner}/{body.repo}"
    if not is_enrolled(repo):
        raise HTTPException(404, "Repository is not enrolled")
    try:
        api = await get_github_app().repo_client(repo)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                409, "Install the GitHub App with access to this repository first"
            ) from e
        raise
    review = await request_github_review(
        db, api, repo, body.pr_number, body.user_id, body.user_name
    )
    return {"id": review.id, "status": review.status, "sha": review.sha}


def verify_signature(body: bytes, signature: str | None) -> None:
    if not settings.github_webhook_secret:
        raise HTTPException(503, "GitHub webhook is not configured")
    expected = (
        "sha256="
        + hmac.new(
            settings.github_webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()
    )
    if not signature or not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Invalid GitHub webhook signature")


async def can_request_review(
    api, repo: str, pr: dict, sender: dict, allow_author: bool
) -> bool:
    if sender.get("type") == "Bot":
        return False
    if allow_author and sender["id"] == pr["user"]["id"]:
        return True
    try:
        result = (
            await api.request(
                "GET", f"repos/{repo}/collaborators/{sender['login']}/permission"
            )
        ).json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return False
        raise
    return result["permission"] in ("admin", "write", "maintain")


@router.post("/github/webhook", status_code=204)
async def github_webhook(request: Request, db: DB):
    raw = await request.body()
    verify_signature(raw, request.headers.get("X-Hub-Signature-256"))
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise HTTPException(400, "Invalid JSON payload") from e
    event = request.headers.get("X-GitHub-Event")
    action = payload.get("action")
    installation_id = payload.get("installation", {}).get("id")
    if event == "installation" and action in ("deleted", "suspend"):
        get_github_app().invalidate(installation_id)
        return Response(status_code=204)
    metadata = payload.get("repository")
    if not metadata or not installation_id or not is_enrolled(metadata["full_name"]):
        return Response(status_code=204)
    repo = metadata["full_name"]
    api = get_github_app().client(installation_id)
    number = None
    sender = payload.get("sender", {})
    if (
        event == "pull_request"
        and settings.github_automatic_reviews
        and action
        in (
            "opened",
            "synchronize",
            "reopened",
            "ready_for_review",
        )
    ):
        number = payload["pull_request"]["number"]
    elif event == "issue_comment" and action == "created":
        issue = payload["issue"]
        if (
            "pull_request" not in issue
            or payload["comment"]["body"].strip() != "/review"
        ):
            return Response(status_code=204)
        pr = (await api.request("GET", f"repos/{repo}/pulls/{issue['number']}")).json()
        if await can_request_review(
            api, repo, pr, sender, settings.github_allow_author_requests
        ):
            number = issue["number"]
    if number is not None:
        try:
            await request_github_review(
                db, api, repo, number, sender["id"], sender["login"]
            )
        except HTTPException as e:
            if e.status_code != 409:
                raise
    return Response(status_code=204)
