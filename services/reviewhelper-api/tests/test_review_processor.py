from unittest.mock import AsyncMock

import pytest
from app import review_processor
from app.config import Settings, settings
from app.database.models import ReviewRequest
from app.enums import Platform
from app.review_processor import (
    build_generated_comment,
    create_main_review_comment,
    format_inline_comment_content,
    summarize_severities,
)

from bugbug.tools.core.data_types import (
    CommentCategory,
    CommentSeverity,
    InlineComment,
)


def make_inline_comment(**overrides) -> InlineComment:
    fields = {
        "filename": "dom/base/Document.cpp",
        "start_line": 42,
        "end_line": 42,
        "content": "Fix the null check.",
        "on_removed_code": False,
        "explanation": "The pointer can be null after `Reset`.",
        "order": 2,
        "severity": CommentSeverity.MAJOR,
        "category": CommentCategory.CORRECTNESS,
    }
    fields.update(overrides)
    return InlineComment(**fields)


def test_build_generated_comment_maps_inline_comment():
    generated_comment = build_generated_comment(7, make_inline_comment())

    assert generated_comment.review_request_id == 7, (
        "The row should belong to the given review request."
    )
    assert generated_comment.on_new is True, (
        "Comments on added code should be stored as `on_new`."
    )


def test_build_generated_comment_keeps_severity_fields():
    generated_comment = build_generated_comment(7, make_inline_comment())

    assert generated_comment.severity == CommentSeverity.MAJOR, (
        "The severity should be persisted."
    )
    assert generated_comment.category == CommentCategory.CORRECTNESS, (
        "The category should be persisted."
    )


def test_severity_frontmatter_enabled_by_default():
    assert Settings.model_fields["severity_frontmatter_enabled"].default is True, (
        "Severity frontmatter should be enabled by default."
    )


def test_format_inline_comment_content_adds_frontmatter(monkeypatch):
    monkeypatch.setattr(settings, "severity_frontmatter_enabled", True)
    generated_comment = build_generated_comment(7, make_inline_comment())

    assert format_inline_comment_content(generated_comment) == (
        "---\n"
        "reviewhelper:\n"
        "  severity: major\n"
        "  category: correctness\n"
        "---\n"
        "Fix the null check."
    ), "The comment should start with the `reviewhelper` frontmatter block."
    assert generated_comment.content == "Fix the null check.", (
        "The stored content should stay raw."
    )


def test_format_inline_comment_content_disabled(monkeypatch):
    monkeypatch.setattr(settings, "severity_frontmatter_enabled", False)
    generated_comment = build_generated_comment(7, make_inline_comment())

    assert format_inline_comment_content(generated_comment) == "Fix the null check.", (
        "Without the setting, the comment should be posted unchanged."
    )


def test_format_inline_comment_content_without_severity(monkeypatch):
    monkeypatch.setattr(settings, "severity_frontmatter_enabled", True)
    generated_comment = build_generated_comment(
        7, make_inline_comment(severity=None, category=None)
    )

    assert format_inline_comment_content(generated_comment) == "Fix the null check.", (
        "Comments without a severity should be posted unchanged."
    )


def make_generated_comments(*severities):
    return [
        build_generated_comment(7, make_inline_comment(severity=severity))
        for severity in severities
    ]


def test_summarize_severities_orders_and_pluralizes():
    generated_comments = make_generated_comments(
        CommentSeverity.NIT,
        CommentSeverity.CRITICAL,
        CommentSeverity.NIT,
        None,
    )

    assert summarize_severities(generated_comments) == "1 critical · 2 nits", (
        "Counts should be ordered by severity, skip empty severities and pluralize nits."
    )


def test_summarize_severities_without_severities():
    assert summarize_severities(make_generated_comments(None)) == "", (
        "No summary should be produced when no comment has a severity."
    )


def test_main_review_comment_includes_findings_summary():
    review_request = ReviewRequest(
        revision_id=284711, diff_id=1234567, general_comment="Looks sound."
    )
    generated_comments = make_generated_comments(
        CommentSeverity.MAJOR, CommentSeverity.MINOR
    )

    main_comment = create_main_review_comment(review_request, generated_comments)

    assert "Looks sound.\n**Findings:** 1 major · 1 minor\n" in main_comment, (
        "The findings summary should follow the general comment."
    )


@pytest.mark.asyncio
async def test_submit_review_to_phabricator_posts_through_async_helpers(monkeypatch):
    create_inline = AsyncMock(side_effect=[101, 102])
    create_comment = AsyncMock()
    monkeypatch.setattr(review_processor, "create_inline_comment", create_inline)
    monkeypatch.setattr(review_processor, "create_revision_comment", create_comment)

    review_request = ReviewRequest(
        platform=Platform.PHABRICATOR,
        revision_id=284711,
        diff_id=1234567,
        general_comment="ok",
    )
    review_request.is_first_published_review = AsyncMock(return_value=True)
    comments = make_generated_comments(CommentSeverity.MAJOR, CommentSeverity.MINOR)
    comments[0].platform_comment_id = 55  # already submitted, must be skipped

    submitted = [
        (comment, comment_id)
        async for comment, comment_id in review_processor.submit_review_to_platform(
            review_request, comments
        )
    ]

    assert submitted == [(comments[1], 101)]
    create_inline.assert_awaited_once()
    assert create_inline.await_args.kwargs["revision_id"] == 284711
    assert create_inline.await_args.kwargs["diff_id"] == 1234567
    create_comment.assert_awaited_once()
    assert create_comment.await_args.args == (284711,)
    assert "(Reviewing [Diff 1234567]" in create_comment.await_args.kwargs["message"]
