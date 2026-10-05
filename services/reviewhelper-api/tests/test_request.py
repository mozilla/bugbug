import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def request_router(monkeypatch):
    for key in (
        "CLOUD_SQL_INSTANCE",
        "DB_USER",
        "DB_PASS",
        "DB_NAME",
        "EXTERNAL_API_KEY",
        "INTERNAL_API_KEY",
        "CLOUD_TASKS_PROJECT",
        "CLOUD_TASKS_LOCATION",
        "CLOUD_TASKS_QUEUE",
        "WORKER_URL",
        "PHABRICATOR_URL",
        "PHABRICATOR_API_KEY",
        "BUGZILLA_URL",
        "BUGZILLA_API_KEY",
    ):
        monkeypatch.setenv(key, "test")

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
async def test_failed_diff_can_be_requested_again(request_router):
    from app.enums import ReviewStatus

    existing = SimpleNamespace(id=50, diff_id=6640, status=ReviewStatus.FAILED)
    db = make_db(existing)

    response = await request_review(request_router, db)

    assert response.status_code == 202
    assert json.loads(response.body)["status"] == "pending"
    db.commit.assert_awaited_once()
    db.add.assert_not_called()
    request_router.create_review_task.assert_awaited_once_with(50)


@pytest.mark.asyncio
async def test_concurrent_retry_of_failed_diff_is_queued_once(request_router):
    from app.enums import ReviewStatus

    existing = SimpleNamespace(
        id=50, diff_id=6640, status=ReviewStatus.FAILED, error="private"
    )
    # Another request requeued the row between our read and our update.
    db = make_db(existing, rowcount=0)

    async def refresh(request):
        request.status = ReviewStatus.PENDING

    db.refresh.side_effect = refresh

    response = await request_review(request_router, db)

    assert response.status_code == 200
    assert json.loads(response.body)["status"] == "pending"
    db.refresh.assert_awaited_once_with(existing)
    db.add.assert_not_called()
    request_router.create_review_task.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["pending", "processing", "retry_pending", "published"]
)
async def test_non_failed_diff_request_is_idempotent(request_router, status):
    from app.enums import ReviewStatus

    existing = SimpleNamespace(id=50, diff_id=6640, status=ReviewStatus(status))
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
