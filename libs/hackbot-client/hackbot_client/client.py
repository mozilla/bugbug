"""Small async client for the public Hackbot API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from hackbot_client.models import RunRef


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
    ) -> RunRef:
        """Create an agent run and return the API's typed run reference."""
        headers = await self._get_headers()
        if on_behalf_of is not None:
            headers["X-On-Behalf-Of"] = on_behalf_of

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                f"{self._base_url}/agents/{agent_name}/runs",
                json=dict(inputs),
                headers=headers,
            )

        response.raise_for_status()
        return RunRef.model_validate(response.json())
