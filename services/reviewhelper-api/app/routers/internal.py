import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_internal_api_key
from app.database.connection import get_db
from app.database.models import ReviewRequest
from app.enums import ReviewStatus
from app.review_processor import process_review, submit_review_to_platform

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(verify_internal_api_key)],
)


@router.get("/process/{review_request_id}")
async def process_review_request(
    review_request_id: int,
    x_cloudtasks_taskname: Annotated[str, Header()],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Process a review request (called by Cloud Tasks).

    This endpoint is protected by internal API key validation.
    """
    # Fetch the review request
    stmt = select(ReviewRequest).where(ReviewRequest.id == review_request_id)
    result = await db.execute(stmt)
    review_request = result.scalar_one_or_none()

    if not review_request:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if not review_request.task_id:
        # We need to tolerate the possibility of delay or failures before
        # committing the transaction that saves the task ID.
        logger.info(
            "Assigning task ID %s to review request %s",
            x_cloudtasks_taskname,
            review_request_id,
        )
        review_request.task_id = x_cloudtasks_taskname

    # Ensure the request is being processed by the correct task
    if review_request.task_id != x_cloudtasks_taskname:
        logger.error(
            "Mismatched task ID for review request %s: expected %s, got %s",
            review_request_id,
            review_request.task_id,
            x_cloudtasks_taskname,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Task ID does not match the one associated with the review request",
        )

    if review_request.status.is_final:
        logger.error(
            "Review request %s is already in final state: %s",
            review_request_id,
            review_request.status,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Review request is not in a processable state",
        )

    if review_request.status == ReviewStatus.COMPLETED:
        # If we already have the generated comments from a previous processing
        # attempt, we can directly submit the review without re-processing it.
        generated_comments = await review_request.awaitable_attrs.comments
    else:
        review_request.status = ReviewStatus.PROCESSING
        await db.commit()

        generated_comments = await process_review(review_request)
        db.add_all(generated_comments)

        review_request.status = ReviewStatus.COMPLETED
        await db.commit()

    for _ in submit_review_to_platform(review_request, generated_comments):
        # We need to commit after each comment is submitted to ensure that the
        # platform_comment_id is saved to the database, which allows the
        # submission process to be safely retried in case of failures.
        await db.commit()

    review_request.status = ReviewStatus.PUBLISHED
