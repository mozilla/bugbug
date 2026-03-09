import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_external_api_key
from app.database.connection import get_db
from app.database.models import ReviewRequest
from app.enums import Platform, ReviewStatus
from app.schemas.review_request import ReviewRequestCreate, ReviewRequestResponse
from app.tasks import create_review_task

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["request"],
    dependencies=[Depends(verify_external_api_key)],
)


@router.post(
    "/request",
    response_model=ReviewRequestResponse,
    response_description="Existing review request found. Returning current status.",
    responses={
        status.HTTP_202_ACCEPTED: {
            "model": ReviewRequestResponse,
            "description": "Review request submitted successfully! It will be processed shortly.",
        },
    },
)
async def create_or_get_review_request(
    request: ReviewRequestCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Submit or check status of a review request.

    - If the request already exists, returns current status/result
    - If new, creates the request with status=pending and queues processing
    """
    stmt = (
        select(ReviewRequest)
        .where(
            ReviewRequest.platform == Platform.PHABRICATOR,
            ReviewRequest.revision_id == request.revision_id,
        )
        .order_by(ReviewRequest.created_at.desc())
        .limit(1)
    )

    existing_request = await db.scalar(stmt)
    # We don't support re-processing the same diff or reviewing older diffs
    if existing_request and (request.diff_id <= existing_request.diff_id):
        return JSONResponse(
            {
                "status": existing_request.status.value,
                "message": _build_response_message(existing_request),
            }
        )

    # Create new request
    review_request = ReviewRequest(
        platform=Platform.PHABRICATOR,
        revision_id=request.revision_id,
        diff_id=request.diff_id,
        user_id=request.user_id,
        user_name=request.user_name,
        acting_capacity=request.acting_capacity,
        reviewer_status=request.reviewer_status,
        status=ReviewStatus.PENDING,
    )
    db.add(review_request)
    await db.commit()

    # Queue task for processing
    await create_review_task(review_request.id)

    return JSONResponse(
        {
            "status": review_request.status.value,
            "message": "Review request submitted successfully! Processing will begin shortly.",
        },
        status.HTTP_202_ACCEPTED,
    )


def _build_response_message(review_request: ReviewRequest) -> str:
    """Build appropriate response message based on review request status."""
    if review_request.status == ReviewStatus.PENDING:
        return f"The review for Diff {review_request.diff_id} is still pending processing. It should begin shortly."

    if review_request.status == ReviewStatus.PROCESSING:
        return f"The review for Diff {review_request.diff_id} is currently being processed. Review Helper will comment once it's done."

    if review_request.status == ReviewStatus.RETRY_PENDING:
        return f"The review for Diff {review_request.diff_id} encountered an issue and will be scheduled for retry."

    if review_request.status == ReviewStatus.PUBLISHED:
        return f"Review Helper already posted its review for Diff {review_request.diff_id}."

    if review_request.status == ReviewStatus.FAILED:
        return f"The review processing for Diff {review_request.diff_id} failed with error: {review_request.error}"

    # This should never happen since we cover all enum values, but we add a fallback just in case.
    logger.warning(
        "The review request %s is in an unexpected state: %s",
        review_request.id,
        review_request.status,
    )
    return f"The review request status for Diff {review_request.diff_id} is '{review_request.status.value}'."
