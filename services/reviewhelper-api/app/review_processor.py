import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Collection

from app.config import settings
from app.database.models import GeneratedComment, ReviewRequest
from app.enums import Platform
from app.github import get_github_app
from app.github_reviews import GitHubTarget, current_pr
from bugbug.tools.core.exceptions import LargeDiffError, RecursionLimitError
from bugbug.tools.core.platforms.phabricator import (
    PhabricatorPatch,
    get_phabricator_client,
)

logger = logging.getLogger(__name__)

# phab-bot can take longer than five minutes to make a revision public.
# The worker returns 409 during this window so Cloud Tasks retries the original
# task using the queue's exponential backoff. On the first attempt after the
# deadline, a still-private revision fails permanently; failed rows stay intact.
VISIBILITY_TIMEOUT = timedelta(minutes=30)


class ReviewProcessingError(Exception):
    """Custom exception for permanent errors during review processing that should not trigger retries."""


class RevisionNotYetPublicError(Exception):
    """The revision is not yet public but was recently created.

    This is a transient error — Cloud Tasks should retry after backoff.
    """


class SupersededReviewError(Exception):
    """The requested GitHub snapshot is no longer eligible for publication."""


def get_github_review_tool(patch):
    from bugbug.tools.code_review import CodeReviewTool
    from bugbug.tools.code_review.langchain_tools import context_tools_for
    from bugbug.tools.core.platforms.github import GitHubCodeContext
    from bugbug.tools.suggestion_filtering import SuggestionFilteringTool

    source = GitHubCodeContext(patch)
    return CodeReviewTool.create(
        todo_enabled=settings.todo_enabled,
        target_software=patch.repo,
        context_tools=context_tools_for(source),
        code_context=source,
        review_comments_db=None,
        skills=[],
        use_static_examples=False,
        suggestion_filterer=SuggestionFilteringTool.create(
            target_software=patch.repo, suggestions_feedback_db=None
        ),
    )


@cache
def get_code_review_tool():
    from bugbug.tools.code_review import CodeReviewTool

    return CodeReviewTool.create(todo_enabled=settings.todo_enabled)


async def process_review(
    review_request: ReviewRequest,
) -> tuple[list[GeneratedComment], str, str, dict]:
    """Process a review request and generate comments.

    Args:
        review_request: The review request to process.

    Returns:
        A tuple of (generated comments, patch summary, general comment, review
        details).
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
    elif review_request.platform == Platform.GITHUB:
        from bugbug.tools.core.platforms.github import (
            GitHubPatch,
            GitHubSnapshotChanged,
        )

        try:
            target = GitHubTarget.from_request(review_request)
        except ValueError as e:
            raise ReviewProcessingError(str(e)) from e
        if await current_pr(review_request) is None:
            raise SupersededReviewError(
                "PR changed, closed, became a draft, or is no longer enrolled"
            )
        try:
            patch = await GitHubPatch.load(
                await get_github_app().repo_client(target.repo),
                target.repo,
                target.number,
                expected_sha=target.sha,
            )
        except GitHubSnapshotChanged as e:
            raise SupersededReviewError(str(e)) from e
        except ValueError as e:
            raise ReviewProcessingError(str(e)) from e
    else:
        raise ValueError(f"Unsupported platform: {review_request.platform}")

    if review_request.platform == Platform.PHABRICATOR and (
        not patch.is_accessible() or not patch.is_public()
    ):
        age = datetime.now(UTC) - review_request.created_at
        if age < VISIBILITY_TIMEOUT:
            raise RevisionNotYetPublicError(
                f"Revision D{review_request.revision_id} is not public. "
                "But the review request was created recently, so this may be a visibility delay."
            )

        raise ReviewProcessingError(
            "Unable to access the revision. This may be because "
            "the revision is private or has restricted visibility."
        )

    tool = (
        get_github_review_tool(patch)
        if review_request.platform == Platform.GITHUB
        else get_code_review_tool()
    )

    try:
        result = await tool.run(patch)
    except LargeDiffError as e:
        raise ReviewProcessingError(
            "The diff size exceeds the current processing limits."
        ) from e
    except RecursionLimitError as e:
        raise ReviewProcessingError(
            "Review Helper could not complete the review within the configured agent step limit."
        ) from e

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

    return (
        generated_comments,
        result.patch_summary,
        result.general_comment,
        result.details,
    )


def submit_review_to_platform(
    review_request: ReviewRequest, generated_comments: Collection[GeneratedComment]
) -> AsyncIterator[tuple[GeneratedComment, int]]:
    """Submit generated comments to the appropriate platform.

    Args:
        review_request: The review request associated with the comments.
        generated_comments: The comments to submit.

    Returns:
        An async iterator of tuples containing the generated comment and the
        inline comment ID assigned by the platform.
    """
    if review_request.platform == Platform.PHABRICATOR:
        return _submit_review_to_phabricator(review_request, generated_comments)
    elif review_request.platform == Platform.GITHUB:
        return _submit_review_to_github(review_request, generated_comments)
    else:
        raise ValueError(f"Unsupported platform: {review_request.platform}")


async def _submit_review_to_phabricator(
    review_request: ReviewRequest, generated_comments: Collection[GeneratedComment]
) -> AsyncIterator[tuple[GeneratedComment, int]]:
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

        # We yield here to allow the caller to save the platform comment id to
        # the database.
        yield comment, phabricator_inline_comment["id"]

    is_first_review = await review_request.is_first_published_review()

    phabricator.request(
        "differential.createcomment",
        revision_id=review_request.revision_id,
        attach_inlines=1,
        message=create_main_review_comment(
            review_request,
            generated_comments,
            include_feedback_note=is_first_review,
        ),
    )


def create_main_review_comment(
    review_request: ReviewRequest,
    generated_comments: Collection[GeneratedComment],
    include_feedback_note: bool = True,
) -> str:
    """Create the main review comment that summarizes the review results."""
    diff_url = f"{settings.phabricator_url}/D{review_request.revision_id}?id={review_request.diff_id}"
    parts = [f"(Reviewing [Diff {review_request.diff_id}]({diff_url}))"]

    if review_request.general_comment:
        parts.append(review_request.general_comment)

    if generated_comments and include_feedback_note:
        if review_request.general_comment:
            parts.append("\n---\n")
        parts.append(
            "(NOTE) Please use {icon thumbs-up} / {icon thumbs-down} reactions on inline comments to provide feedback. "
            "This will have a significant impact on the quality of future reviews."
        )

    return "\n".join(parts)


async def _submit_review_to_github(
    review_request: ReviewRequest,
    generated_comments: Collection[GeneratedComment],
) -> AsyncIterator[tuple[GeneratedComment, int]]:
    """Publish one review; reconcile an uncertain write before attempting another."""
    from bugbug.tools.core.platforms.github import GitHubSnapshotChanged
    from bugbug.tools.core.platforms.github_review import (
        GitHubReviewPublisher,
        GitHubReviewRejected,
    )

    app = get_github_app()
    try:
        target = GitHubTarget.from_request(review_request)
    except ValueError as e:
        raise ReviewProcessingError(str(e)) from e
    publisher = GitHubReviewPublisher(
        await app.repo_client(target.repo), await app.bot_login()
    )
    comments = [
        {
            "path": comment.file_path,
            "line": comment.line_start,
            "side": "RIGHT",
            "body": comment.content,
        }
        for comment in generated_comments
        if comment.on_new and comment.line_start == comment.line_end
    ]

    async def is_current() -> bool:
        return await current_pr(review_request) is not None

    body = f"Reviewing `{target.sha[:12]}`.\n\n"
    body += review_request.general_comment or "Automated review completed."
    try:
        published = await publisher.publish(
            repo=target.repo,
            number=target.number,
            sha=target.sha,
            marker=f"<!-- reviewhelper:{review_request.id} -->",
            body=body,
            comments=comments,
            is_current=is_current,
        )
    except GitHubSnapshotChanged as e:
        raise SupersededReviewError(str(e)) from e
    except GitHubReviewRejected as e:
        raise ReviewProcessingError(str(e)) from e
    for comment in generated_comments:
        if comment.platform_comment_id:
            continue
        match = next(
            (
                c
                for c in published
                if c["path"] == comment.file_path
                and (c.get("line") or c.get("original_line")) == comment.line_start
                and c["body"] == comment.content
            ),
            None,
        )
        if match is not None:
            published.remove(match)
            yield comment, match["id"]
