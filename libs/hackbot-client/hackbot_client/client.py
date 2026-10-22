"""Small async client for the public Hackbot API."""

import asyncio
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import httpx
from google.auth import compute_engine, credentials
from google.auth.transport import requests as google_requests
from google.oauth2 import service_account

from hackbot_client.models import ApplyActionsResponse, TriggeredRun


class HackbotClient:
    """Call the public Hackbot API with either API key or service account auth."""

    def __init__(
        self,
        base_url: str = "https://hackbot-api.moz.tools",
        api_key: str = "",
        key_file: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        """Initialize the client.

        Auth is picked in this order: API key, service account key file, then
        the GCP metadata server. Service account tokens use `base_url` as
        their audience.

        Args:
            base_url: Base URL of the Hackbot API
            api_key: API key for legacy authentication (optional)
            key_file: Path to a service account key file, for running outside
                GCP without a metadata server (optional)
            timeout_seconds: HTTP request timeout
        """
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._key_file = key_file
        self._timeout_seconds = timeout_seconds
        self._credentials: credentials.Credentials | None = None

    def _refresh_credentials(self) -> None:
        request = google_requests.Request()
        if self._credentials is None:
            if self._key_file:
                self._credentials = (
                    service_account.IDTokenCredentials.from_service_account_file(
                        self._key_file, target_audience=self._base_url
                    )
                )
            else:
                self._credentials = compute_engine.IDTokenCredentials(
                    request,
                    target_audience=self._base_url,
                    use_metadata_identity_endpoint=True,
                )
        self._credentials.refresh(request)

    async def _get_headers(self) -> dict[str, str]:
        """Return auth headers: API key or service account token."""
        if self._api_key:
            return {"X-API-Key": self._api_key}

        if (
            self._credentials is None
            or self._credentials.token_state == credentials.TokenState.INVALID
        ):
            await asyncio.to_thread(self._refresh_credentials)
        return {"Authorization": f"Bearer {self._credentials.token}"}

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
                headers=await self._get_headers(),
            )

        response.raise_for_status()
        return ApplyActionsResponse.model_validate(response.json())
