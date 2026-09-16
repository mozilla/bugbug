"""Small async client for the public Hackbot API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from hackbot_client.models import RunRef


class HackbotClient:
    """Call the public, API-key-authenticated Hackbot endpoints."""

    def __init__(
        self, base_url: str, api_key: str, timeout_seconds: float = 30.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    async def trigger_run(
        self,
        agent_name: str,
        inputs: Mapping[str, Any],
        *,
        on_behalf_of: str | None = None,
        dedupe_key: str | None = None,
    ) -> RunRef:
        """Create an agent run and return the API's typed run reference.

        `dedupe_key` names the work the run does, and the name belongs to one
        run for good: repeated triggers for the key will be no-ops, and the same
        run reference will be returned.
        """
        headers = {"X-API-Key": self._api_key}
        if on_behalf_of is not None:
            headers["X-On-Behalf-Of"] = on_behalf_of

        params = {} if dedupe_key is None else {"dedupe_key": dedupe_key}

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                f"{self._base_url}/agents/{agent_name}/runs",
                json=dict(inputs),
                headers=headers,
                params=params,
            )

        response.raise_for_status()
        return RunRef.model_validate(response.json())
