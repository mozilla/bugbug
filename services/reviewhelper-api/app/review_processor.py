import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Collection, Iterable

from app.config import settings
from app.database.models import GeneratedComment, ReviewRequest
from app.enums import Platform
from app.reviewer_groups import get_reviewer_groups_config, matching_groups
from bugbug.tools.code_review.diff_analysis import (
    analyze_diff,
    format_test_signal_block,
)
from bugbug.tools.code_review.scoring import RiskComplexityScorer
from bugbug.tools.code_review.test_coverage import (
    format_coverage_block,
    lookup_existing_coverage,
)
from bugbug.tools.core.exceptions import LargeDiffError, RecursionLimitError
from bugbug.tools.core.platforms.phabricator import (
    PhabricatorPatch,
    get_phabricator_client,
    get_project_members,
    resolve_project_phid,
)

logger = logging.getLogger(__name__)

VISIBILITY_TIMEOUT = timedelta(minutes=5)


class ReviewProcessingError(Exception):
    """Custom exception for permanent errors during review processing that should not trigger retries."""


class RevisionNotYetPublicError(Exception):
    """The revision is not yet public but was recently created.

    This is a transient error — Cloud Tasks should retry after backoff.
    """


class ReviewSkipped(Exception):
    """The revision was intentionally not reviewed (e.g. gated out by scores).

    Carries the reason and the details to persist. Not an error: the caller
    records it as a terminal SKIPPED status without retrying.
    """

    def __init__(self, reason: str, *, details: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


@cache
def get_code_review_tool():
    from bugbug.tools.code_review import CodeReviewTool

    return CodeReviewTool.create(todo_enabled=settings.todo_enabled)


@cache
def get_risk_scorer() -> RiskComplexityScorer:
    return RiskComplexityScorer.create(
        model=settings.scoring_model,
        max_tokens=settings.scoring_max_tokens,
    )


async def process_review(
    review_request: ReviewRequest,
) -> tuple[list[GeneratedComment], str, dict]:
    """Process a review request and generate comments.

    Args:
        review_request: The review request to process.

    Returns:
        A tuple of (generated comments, patch summary, review details).
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

    if not patch.is_accessible() or not patch.is_public():
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

    test_signals_block, scoring_details = await _score_and_gate(patch, review_request)

    tool = get_code_review_tool()

    try:
        result = await tool.run(patch, test_signals_block=test_signals_block)
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

    details = {**result.details, **scoring_details}

    return generated_comments, result.patch_summary, details


async def _score_and_gate(
    patch: PhabricatorPatch, review_request: ReviewRequest
) -> tuple[str, dict]:
    """Score the revision's risk/complexity and decide whether to review it.

    Records the scores and test signals on ``review_request`` (so they're
    persisted even when skipped) and returns the rendered ``<test_signals>``
    block plus the scoring details to merge into the review details. Raises
    ``ReviewSkipped`` when either score is at or above the group's threshold.
    """
    config = get_reviewer_groups_config()

    # Only revisions requesting review from an enabled group are auto-reviewed.
    enabled_groups = [group for group in matching_groups(patch) if group.enabled]
    if not enabled_groups:
        raise ReviewSkipped("not_enabled")

    # First enabled matching group acts as the primary.
    primary = enabled_groups[0]

    # Cheap author gates run before the (paid) scoring call.
    _enforce_author_gates(primary, patch)

    risk_threshold = primary.effective_risk_threshold(config.defaults)
    complexity_threshold = primary.effective_complexity_threshold(config.defaults)

    diff_stats = analyze_diff(patch.raw_diff)
    coverage = await lookup_existing_coverage(diff_stats.non_test_paths)
    test_signals_block = format_test_signal_block(
        diff_stats, coverage_block=format_coverage_block(coverage)
    )

    scorer = get_risk_scorer()
    scoring = await scorer.run(
        title=patch.patch_title,
        summary=patch.summary,
        revision_id=patch.revision_id,
        author=patch.author_phid,
        bug_id=patch.bug_id if patch.has_bug else None,
        raw_diff=patch.raw_diff,
        test_signals_block=test_signals_block,
    )
    scores = scoring.scores

    # Persist the scalar signals on the request so they survive a skip and feed
    # the dashboard; verbose factors/coverage go in the details JSON.
    review_request.risk = scores.risk
    review_request.complexity = scores.complexity
    review_request.in_diff_test_signal = diff_stats.in_diff_test_signal
    review_request.coverage_signal = coverage.coverage_signal

    scoring_details = {
        "scoring_model": scoring.model,
        "scoring_usage": scoring.usage,
        "risk_factors": scores.risk_factors,
        "complexity_factors": scores.complexity_factors,
        "coverage": {
            "covered_paths": coverage.covered_paths,
            "uncovered_paths": coverage.uncovered_paths,
            "candidate_count": coverage.candidate_count,
        },
        "thresholds": {
            "risk": risk_threshold,
            "complexity": complexity_threshold,
            "group": primary.slug,
        },
    }

    if scores.risk >= risk_threshold or scores.complexity >= complexity_threshold:
        logger.info(
            "Skipping review for D%s: risk=%s complexity=%s (thresholds %s/%s)",
            review_request.revision_id,
            scores.risk,
            scores.complexity,
            risk_threshold,
            complexity_threshold,
        )
        raise ReviewSkipped("above_threshold", details=scoring_details)

    return test_signals_block, scoring_details


def _enforce_author_gates(group, patch: PhabricatorPatch) -> None:
    """Skip review based on the revision author's relationship to the group.

    Raises ``ReviewSkipped`` when the author has opted out, or when the group
    restricts review to its own members and the author isn't one.
    """
    author_phid = patch.author_phid

    if author_phid in group.opt_out:
        raise ReviewSkipped("author_opted_out")

    if group.restrict_to_member_authors:
        group_phid = resolve_project_phid(group.slug)
        members = get_project_members(group_phid) if group_phid else frozenset()
        # If the membership lookup is empty (transient failure / unresolved
        # project), don't skip — we'd rather over-include than silently drop
        # everything for the group.
        if members and author_phid not in members:
            raise ReviewSkipped("author_not_in_group")


def submit_review_to_platform(
    review_request: ReviewRequest, generated_comments: Iterable[GeneratedComment]
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
        message=(
            create_main_review_comment(review_request, generated_comments)
            if is_first_review
            else None
        ),
    )


def create_main_review_comment(
    review_request: ReviewRequest, generated_comments: Collection[GeneratedComment]
) -> str:
    """Create the main review comment that summarizes the review results."""
    diff_url = f"{settings.phabricator_url}/D{review_request.revision_id}?id={review_request.diff_id}"
    parts = [f"(Reviewing [Diff {review_request.diff_id}]({diff_url}))"]

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
