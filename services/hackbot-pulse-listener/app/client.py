import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)


def _headers() -> dict[str, str]:
    return {"X-API-Key": settings.hackbot_api_key}


def trigger_run(inputs: dict, agent_name: str | None = None) -> str | None:
    """Create an agent run. Returns the run id, or None in dry-run mode."""
    agent = agent_name or settings.agent_name
    if settings.dry_run:
        logger.info("[dry-run] would trigger %s run: %s", agent, inputs)
        return None

    url = f"{settings.hackbot_api_url}/agents/{agent}/runs"
    resp = httpx.post(url, json=inputs, headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["run_id"]


def get_run(run_id: str) -> dict:
    url = f"{settings.hackbot_api_url}/runs/{run_id}"
    resp = httpx.get(url, headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_artifact(run_id: str, name: str) -> str | None:
    """Download a run artifact's text content, or None if it is missing."""
    url = f"{settings.hackbot_api_url}/runs/{run_id}/artifacts/{name}"
    resp = httpx.get(url, headers=_headers(), timeout=_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    download = httpx.get(resp.json()["url"], timeout=_TIMEOUT)
    download.raise_for_status()
    return download.text
