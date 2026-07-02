import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)


def _headers() -> dict[str, str]:
    return {"X-API-Key": settings.hackbot_api_key}


def trigger_run(inputs: dict) -> str | None:
    """Create a build-repair run. Returns the run id, or None in dry-run mode."""
    if settings.dry_run:
        logger.info("[dry-run] would trigger %s run: %s", settings.agent_name, inputs)
        return None

    url = f"{settings.hackbot_api_url}/agents/{settings.agent_name}/runs"
    resp = httpx.post(url, json=inputs, headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["run_id"]
