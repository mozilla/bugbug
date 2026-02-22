import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_internal_api_key
from app.database.connection import get_db
from app.database.models import ReviewRequest
from app.enums import ReviewStatus
from app.review_processor import process_review, submit_review_to_platform
from bugbug.tools.core.exceptions import LargeDiffError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(verify_internal_api_key)],
)

RECOVERY_BACKOFF_DELAY = timedelta(minutes=30)


async def claim_review_request(db: AsyncSession, review_request: ReviewRequest) -> bool:
    """Claim a review request for processing.

    Uses optimistic concurrency: succeeds only if the row's status and
    updated_at haven't changed since we last read them.
    """
    stmt = (
        update(ReviewRequest)
        .where(
            ReviewRequest.id == review_request.id,
            ReviewRequest.status == review_request.status,
            ReviewRequest.updated_at == review_request.updated_at,
        )
        .values(status=ReviewStatus.PROCESSING)
    )
    result = await db.execute(stmt)
    await db.commit()

    if result.rowcount == 0:
        return False

    await db.refresh(review_request)
    return True


@router.get("/process/{review_request_id}")
async def process_review_request(
    review_request_id: int,
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
        logger.error("Review request %s not found", review_request_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if review_request.status.is_final:
        logger.error(
            "Review request %s is already in final state: %s",
            review_request_id,
            review_request.status,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # If another worker is actively processing, return 509 so Cloud Tasks retries.
    # If it's been stuck in PROCESSING for too long, the worker likely crashed —
    # fall through and try to reclaim it.
    if review_request.status == ReviewStatus.PROCESSING:
        age = datetime.now(UTC) - review_request.updated_at
        if age < RECOVERY_BACKOFF_DELAY:
            logger.warning(
                "Review request %s is in PROCESSING state and was updated %s ago",
                review_request_id,
                age,
            )
            return Response(status_code=status.HTTP_409_CONFLICT)

    if not await claim_review_request(db, review_request):
        logger.warning(
            "Review request %s was modified concurrently, likely claimed by another worker",
            review_request_id,
        )
        return Response(status_code=status.HTTP_409_CONFLICT)

    if review_request.summary:
        logger.info(
            "Review request %s already has a summary, skipping AI processing",
            review_request_id,
        )
        comments = await review_request.awaitable_attrs.comments
    else:
        try:
            comments, patch_summary, details = await process_review(review_request)
        except LargeDiffError:
            review_request.error = (
                "The diff size exceeds the current processing limits."
            )
            review_request.status = ReviewStatus.FAILED
            await db.commit()
            # We return 204 here to avoid triggering retries, since this is a
            # permanent failure that won't be resolved by retrying.
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        except Exception:
            review_request.status = ReviewStatus.RETRY_PENDING
            await db.commit()
            raise

        db.add_all(comments)
        review_request.summary = patch_summary
        review_request.details = details
        await db.commit()

    for generated_comment, inline_comment_id in submit_review_to_platform(
        review_request, comments
    ):
        # We need to commit after each comment is submitted to ensure that the
        # platform_comment_id is saved to the database, which allows the
        # submission process to be safely retried in case of failures.
        generated_comment.platform_comment_id = inline_comment_id
        await db.commit()

    review_request.status = ReviewStatus.PUBLISHED
    await db.commit()
