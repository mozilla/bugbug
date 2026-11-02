import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)


def _headers() -> dict[str, str]:
    return {"X-API-Key": settings.hackbot_api_key}


def trigger_run(inputs: dict, agent_name: str | None = None) -> str | None:
    """Create an agent run. Returns the run id, or None in dry-run mode.

    The model/turn overrides are settings shared by every agent, so they are added
    here rather than by each caller.
    """
    agent = agent_name or settings.agent_name
    inputs = dict(inputs)
    if settings.model:
        inputs["model"] = settings.model
    if settings.max_turns is not None:
        inputs["max_turns"] = settings.max_turns
    if settings.dry_run:
        logger.info("[dry-run] would trigger %s run: %s", agent, inputs)
        return None

    url = f"{settings.hackbot_api_url}/agents/{agent}/runs"
    resp = httpx.post(url, json=inputs, headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["run_id"]
