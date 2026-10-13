from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("accessible,public", [(False, False), (True, False)])
@pytest.mark.parametrize("age_minutes", [0, 5, 10, 29, 30, 60])
async def test_visibility_retry_window(monkeypatch, accessible, public, age_minutes):
    from app import review_processor
    from app.enums import Platform

    now = datetime(2026, 10, 13, tzinfo=UTC)
    clock = Mock()
    clock.now.return_value = now
    monkeypatch.setattr(review_processor, "datetime", clock)
    patch = Mock()
    patch.is_accessible.return_value = accessible
    patch.is_public.return_value = public
    monkeypatch.setattr(review_processor, "PhabricatorPatch", Mock(return_value=patch))
    tool_factory = Mock()
    monkeypatch.setattr(review_processor, "get_code_review_tool", tool_factory)
    request = SimpleNamespace(
        id=1,
        platform=Platform.PHABRICATOR,
        revision_id=2,
        diff_id=3,
        created_at=now - timedelta(minutes=age_minutes),
    )
    expected_error = (
        review_processor.RevisionNotYetPublicError
        if age_minutes < 30
        else review_processor.ReviewProcessingError
    )
    with pytest.raises(expected_error):
        await review_processor.process_review(request)
    tool_factory.assert_not_called()


@pytest.mark.asyncio
async def test_revision_becomes_public_during_retry_window(monkeypatch):
    from app import review_processor
    from app.enums import Platform

    patch = Mock()
    patch.is_accessible.return_value = True
    patch.is_public.return_value = True
    monkeypatch.setattr(review_processor, "PhabricatorPatch", Mock(return_value=patch))
    tool = Mock()
    tool.run = AsyncMock(
        return_value=SimpleNamespace(
            review_comments=[],
            patch_summary="summary",
            general_comment="",
            details={},
        )
    )
    monkeypatch.setattr(
        review_processor, "get_code_review_tool", Mock(return_value=tool)
    )
    request = SimpleNamespace(
        id=1,
        platform=Platform.PHABRICATOR,
        revision_id=2,
        diff_id=3,
        created_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    assert await review_processor.process_review(request) == ([], "summary", "", {})
    tool.run.assert_awaited_once_with(patch)


@pytest.mark.asyncio
async def test_visibility_retries_original_request_until_deadline(monkeypatch):
    from app import review_processor
    from app.database.models import ReviewRequest
    from app.enums import Platform, ReviewStatus
    from app.routers import internal

    created_at = datetime(2026, 10, 13, tzinfo=UTC)
    request = ReviewRequest(
        id=1,
        platform=Platform.PHABRICATOR,
        revision_id=2,
        diff_id=3,
        user_id=4,
        user_name="tester",
        created_at=created_at,
        updated_at=created_at,
        status=ReviewStatus.PENDING,
    )
    result = Mock(rowcount=1)
    result.scalar_one_or_none.return_value = request
    db = AsyncMock()
    db.execute.return_value = result
    db.info = {}
    db.add = Mock()
    db.add_all = Mock()
    patch = Mock()
    patch.is_accessible.return_value = True
    patch.is_public.return_value = False
    patch_factory = Mock(return_value=patch)
    monkeypatch.setattr(review_processor, "PhabricatorPatch", patch_factory)
    tool_factory = Mock()
    monkeypatch.setattr(review_processor, "get_code_review_tool", tool_factory)
    clock = Mock()
    monkeypatch.setattr(review_processor, "datetime", clock)

    # Each dispatch reuses the original request. Cloud Tasks controls when
    # these dispatches occur; the worker determines when visibility is terminal.
    for age_minutes in (1, 10, 29):
        clock.now.return_value = created_at + timedelta(minutes=age_minutes)
        response = await internal.process_review_request(request.id, db)
        assert response.status_code == 409
        assert request.status == ReviewStatus.RETRY_PENDING
        assert request.created_at == created_at
        assert request.error is None

    clock.now.return_value = created_at + timedelta(minutes=31)
    response = await internal.process_review_request(request.id, db)
    assert response.status_code == 204
    assert request.status == ReviewStatus.FAILED
    assert "private" in request.error
    assert request.created_at == created_at
    assert request.user_id == 4

    # A duplicate dispatch after failure acknowledges the task without
    # resetting the row or checking visibility again.
    response = await internal.process_review_request(request.id, db)
    assert response.status_code == 204
    assert request.status == ReviewStatus.FAILED
    assert patch_factory.call_count == 4
    tool_factory.assert_not_called()
    db.add_all.assert_not_called()
