import logging
from datetime import datetime
from functools import cache

from google.api_core.exceptions import AlreadyExists
from google.cloud.tasks_v2 import (
    CloudTasksAsyncClient,
    HttpMethod,
    HttpRequest,
    Task,
)
from google.protobuf.duration_pb2 import Duration

from app.config import settings

logger = logging.getLogger(__name__)


@cache
def _get_tasks_client():
    return CloudTasksAsyncClient()


async def create_review_task(
    review_request_id: int,
    schedule_time: datetime | None = None,
    *,
    deduplicate: bool = False,
):
    """Create a Cloud Task to process a review request.

    Args:
        review_request_id: The ID of the review request to process.
        schedule_time: Optional time to delay task dispatch until.
        deduplicate: Use a stable task name for repeated request deliveries.
    """
    client = _get_tasks_client()

    parent = client.queue_path(
        settings.cloud_tasks_project,
        settings.cloud_tasks_location,
        settings.cloud_tasks_queue,
    )

    url = f"{settings.worker_url}/internal/process/{review_request_id}"

    task = Task(
        name=f"{parent}/tasks/review-{review_request_id}" if deduplicate else "",
        http_request=HttpRequest(
            http_method=HttpMethod.GET,
            url=url,
            headers={
                "Authorization": f"Bearer {settings.internal_api_key}",
            },
        ),
        dispatch_deadline=Duration(seconds=30 * 60),
        schedule_time=schedule_time,
    )

    try:
        response = await client.create_task(parent=parent, task=task)
    except AlreadyExists:
        if deduplicate:
            return
        raise
    logger.info(
        "Created task %s for review request %s", response.name, review_request_id
    )
