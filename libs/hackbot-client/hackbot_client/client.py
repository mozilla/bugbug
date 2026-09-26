"""Small async client for the public Hackbot API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from hackbot_client.models import ApplyActionsResponse, TriggeredRun


class HackbotClient:
    """Call the public Hackbot API with either API key or service account auth."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        audience: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        """Initialize the client.

        Args:
            base_url: Base URL of the Hackbot API
            api_key: API key for legacy authentication (optional)
            audience: Audience for service account OIDC token minting.
                Required if api_key is not set.
            timeout_seconds: HTTP request timeout
        """
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._audience = audience
        self._timeout_seconds = timeout_seconds

        if not api_key and not audience:
            raise ValueError(
                "Either api_key or audience must be provided for authentication"
            )

    async def _get_headers(self) -> dict[str, str]:
        """Return auth headers: API key or service account token."""
        if self._api_key:
            return {"X-API-Key": self._api_key}

        token = await asyncio.to_thread(
            id_token.fetch_id_token,
            google_requests.Request(),
            self._audience,
        )
        return {"Authorization": f"Bearer {token}"}

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
        headers = await self._get_headers()
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
