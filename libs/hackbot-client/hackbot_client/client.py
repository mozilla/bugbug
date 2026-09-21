"""Small async client for the public Hackbot API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import httpx

from hackbot_client.models import ApplyActionsResponse, TriggeredRun


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
    ) -> TriggeredRun:
        """Create an agent run and return the API's typed run reference.

        `dedupe_key` keys the work the run does, and a key belongs to one run
        for good: repeated triggers carrying it are no-ops, answered with the
        same run reference and `is_new=False`.
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
        # The API distinguishes the two outcomes only by status code: `201` for the
        # run this request started, `200` for one a `dedupe_key` collapsed onto.
        return TriggeredRun.model_validate(
            {**response.json(), "is_new": response.status_code == 201}
        )

    async def apply_actions(self, run_id: str | UUID) -> ApplyActionsResponse:
        """Apply every one of a run's actions that has not landed yet.

        Idempotent, because the API skips rows already marked applied: calling
        this on a run whose actions all landed is a no-op.
        """
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                f"{self._base_url}/runs/{run_id}/actions/apply",
                headers={"X-API-Key": self._api_key},
            )

        response.raise_for_status()
        return ApplyActionsResponse.model_validate(response.json())
