"""Small async client for the public Hackbot API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import httpx

from hackbot_client.models import ApplyActionsResponse, TriggeredRun


class HackbotAPIError(Exception):
    """A request to the Hackbot API failed (or answered with something unusable)."""


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

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/agents/{agent_name}/runs",
                    json=dict(inputs),
                    headers=headers,
                    params=params,
                )
        except httpx.HTTPError as exc:
            raise HackbotAPIError(f"Hackbot API request failed: {exc}") from exc

        if not response.is_success:
            raise HackbotAPIError(
                f"Hackbot API returned HTTP {response.status_code} "
                f"({response.reason_phrase}) for {response.request.url}"
            )

        # The API distinguishes the two outcomes only by status code: `201` for the
        # run this request started, `200` for one a `dedupe_key` collapsed onto.
        try:
            return TriggeredRun.model_validate(
                {**response.json(), "is_new": response.status_code == 201}
            )
        except (ValueError, TypeError) as exc:
            raise HackbotAPIError(
                f"Hackbot API returned an invalid run (HTTP {response.status_code})"
            ) from exc

    async def apply_actions(self, run_id: str | UUID) -> ApplyActionsResponse:
        """Apply every one of a run's actions that has not landed yet.

        Idempotent, because the API skips rows already marked applied: calling
        this on a run whose actions all landed is a no-op.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/runs/{run_id}/actions/apply",
                    headers={"X-API-Key": self._api_key},
                )
        except httpx.HTTPError as exc:
            raise HackbotAPIError(f"Hackbot API request failed: {exc}") from exc

        if not response.is_success:
            raise HackbotAPIError(
                f"Hackbot API returned HTTP {response.status_code} "
                f"({response.reason_phrase}) for {response.request.url}"
            )

        try:
            return ApplyActionsResponse.model_validate(response.json())
        except ValueError as exc:
            raise HackbotAPIError(
                "Hackbot API returned an invalid action list "
                f"(HTTP {response.status_code})"
            ) from exc
