import logging
from functools import cache
from typing import Collection, Iterable

from app.database.models import GeneratedComment, ReviewRequest
from app.enums import Platform
from bugbug.tools.core.platforms.phabricator import (
    PhabricatorPatch,
    get_phabricator_client,
)

logger = logging.getLogger(__name__)


class ReviewProcessingError(Exception):
    """Custom exception for review processing errors."""


@cache
def get_code_review_tool():
    from bugbug.tools.code_review import CodeReviewTool

    return CodeReviewTool.create()


async def process_review(review_request: ReviewRequest) -> list[GeneratedComment]:
    """Process a review request and generate comments.

    Args:
        review_request: The review request to process.

    Returns:
        The generated comments from the review processing.
    """
    logger.info(
        "Processing review request %s for platform %s",
        review_request.id,
        review_request.platform,
    )

    if review_request.platform == Platform.PHABRICATOR:
        patch = PhabricatorPatch(
            revision_id=review_request.revision_id, diff_id=review_request.diff_id
        )
    else:
        raise ValueError(f"Unsupported platform: {review_request.platform}")

    tool = get_code_review_tool()

    result = await tool.run(patch)

    generated_comments = [
        GeneratedComment(
            review_request_id=review_request.id,
            file_path=comment.filename,
            line_start=comment.start_line,
            line_end=comment.end_line,
            on_new=not comment.on_removed_code,
            content=comment.content,
            details=comment.details,
        )
        for comment in result.review_comments
    ]

    review_request.summary = result.patch_summary
    review_request.details = result.details

    return generated_comments


def submit_review_to_platform(
    review_request: ReviewRequest, generated_comments: Iterable[GeneratedComment]
) -> Iterable[int]:
    """Submit generated comments to the appropriate platform.

    Args:
        review_request: The review request associated with the comments.
        generated_comments: The comments to submit.

    Returns:
        IDs of the submitted comments on the platform.
    """
    if review_request.platform == Platform.PHABRICATOR:
        yield from _submit_review_to_phabricator(review_request, generated_comments)
    else:
        raise ValueError(f"Unsupported platform: {review_request.platform}")


def _submit_review_to_phabricator(
    review_request: ReviewRequest, generated_comments: Collection[GeneratedComment]
) -> Iterable[int]:
    """Submit generated comments to Phabricator."""
    phabricator = get_phabricator_client()

    for comment in generated_comments:
        if comment.platform_comment_id:
            # This could happen if the submission process is retried after a
            # failure in the middle of submitting comments.
            logger.info(
                "Comment %s already has a platform comment ID: %s. Skipping submission.",
                comment.id,
                comment.platform_comment_id,
            )
            continue

        phabricator_inline_comment = phabricator.request(
            "differential.createinline",
            revisionID=review_request.revision_id,
            diffID=review_request.diff_id,
            filePath=comment.file_path,
            isNewFile=comment.on_new,
            lineNumber=comment.line_start,
            lineLength=comment.line_end - comment.line_start,
            content=comment.content,
        )
        comment.platform_comment_id = phabricator_inline_comment["id"]

        # We yield here to allow the caller to commit the updated
        # platform_comment_id to the database
        yield

    phabricator.request(
        "differential.createcomment",
        revision_id=review_request.revision_id,
        attach_inlines=1,
        message=create_main_review_comment(review_request, generated_comments),
    )


def create_main_review_comment(
    review_request: ReviewRequest, generated_comments: Collection[GeneratedComment]
) -> str:
    """Create the main review comment that summarizes the review results."""
    parts = []

    if review_request.summary:
        parts.append(review_request.summary)
        parts.append("\n---\n")

    num_comments = len(generated_comments)
    if num_comments > 0:
        parts.append(
            "(NOTE) Please use {icon thumbs-up} / {icon thumbs-down} reactions on inline comments to provide feedback. "
            "This will have a significant impact on the quality of future reviews."
        )
    else:
        parts.append("(NOTE) Automated review completed with no comments.")

    return "\n".join(parts)
