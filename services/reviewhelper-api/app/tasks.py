import logging
from functools import cache

from google.cloud.tasks_v2 import (
    CloudTasksAsyncClient,
    HttpMethod,
    HttpRequest,
    Task,
)

from app.config import settings

logger = logging.getLogger(__name__)


@cache
def _get_tasks_client():
    return CloudTasksAsyncClient()


async def create_review_task(review_request_id: int) -> str | None:
    """Create a Cloud Task to process a review request.

    Args:
        review_request_id: The ID of the review request to process.

    Returns:
        The name of the created task.
    """
    client = _get_tasks_client()

    parent = client.queue_path(
        settings.cloud_tasks_project,
        settings.cloud_tasks_location,
        settings.cloud_tasks_queue,
    )

    url = f"{settings.worker_url}/internal/process/{review_request_id}"

    task = Task(
        http_request=HttpRequest(
            http_method=HttpMethod.GET,
            url=url,
            headers={
                "Authorization": f"Bearer {settings.internal_api_key}",
            },
        ),
    )

    response = await client.create_task(parent=parent, task=task)
    logger.info(
        "Created task %s for review request %s", response.name, review_request_id
    )

    # Return only the task ID
    return response.name.split("/")[-1]
