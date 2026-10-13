import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def request_router(monkeypatch):
    from app.routers import request as request_router

    monkeypatch.setattr(request_router, "create_review_task", AsyncMock())
    return request_router


def make_db(existing, rowcount=1):
    db = AsyncMock()
    db.scalar.return_value = existing
    db.execute.return_value = SimpleNamespace(rowcount=rowcount)
    db.add = Mock()
    return db


async def request_review(request_router, db, diff_id=6640):
    from app.schemas.review_request import ReviewRequestCreate

    return await request_router.create_or_get_review_request(
        ReviewRequestCreate(
            revision_id=4232,
            diff_id=diff_id,
            user_id=1,
            user_name="tester",
        ),
        db,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["pending", "processing", "retry_pending", "published", "failed"]
)
async def test_existing_diff_request_is_idempotent(request_router, status):
    from app.enums import ReviewStatus

    existing = SimpleNamespace(
        id=50, diff_id=6640, status=ReviewStatus(status), error="private"
    )
    db = make_db(existing)

    response = await request_review(request_router, db)

    assert response.status_code == 200
    assert json.loads(response.body)["status"] == status
    db.execute.assert_not_awaited()
    db.add.assert_not_called()
    request_router.create_review_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_older_diff_does_not_retry_failed_request(request_router):
    from app.enums import ReviewStatus

    existing = SimpleNamespace(
        id=50, diff_id=6640, status=ReviewStatus.FAILED, error="private"
    )
    db = make_db(existing)

    response = await request_review(request_router, db, diff_id=6639)

    assert response.status_code == 200
    assert json.loads(response.body)["status"] == "failed"
    db.execute.assert_not_awaited()
    request_router.create_review_task.assert_not_awaited()
