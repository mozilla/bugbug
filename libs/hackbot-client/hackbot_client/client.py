"""Small async client for the public Hackbot API."""

import asyncio
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
from google.auth import compute_engine, credentials
from google.auth.transport import requests as google_requests
from google.oauth2 import service_account
from pydantic import BaseModel, ValidationError

from hackbot_client.models import (
    AgentDescriptor,
    ApplyActionsResponse,
    RunDoc,
    RunStatus,
    TriggeredRun,
)


class HackbotAPIError(Exception):
    """A request to the Hackbot API failed (or answered with something unusable).

    `status_code` is the API's HTTP status when it answered with an error, and
    `detail` the reason it gave, if any; both are None when it never answered.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class HackbotInvalidResponseError(HackbotAPIError):
    """The Hackbot API answered successfully, but with a body that is unusable."""


def _error_detail(response: httpx.Response) -> str | None:
    """The reason an error response gives, from FastAPI's `{"detail": ...}`."""
    try:
        body = response.json()
    except ValueError:
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    if detail is None:
        return None
    return detail if isinstance(detail, str) else json.dumps(detail)


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
        self._http = httpx.AsyncClient(timeout=timeout_seconds)
        self._credentials: credentials.Credentials | None = None

    async def close(self) -> None:
        await self._http.aclose()

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

    async def _send(
        self,
        method: str,
        path: str,
        *,
        on_behalf_of: str | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        """Send one request, raising `HackbotAPIError` unless it succeeded."""
        headers = await self._get_headers()
        if on_behalf_of is not None:
            headers["X-On-Behalf-Of"] = on_behalf_of

        kwargs: dict[str, Any] = {"headers": headers}
        if params is not None:
            kwargs["params"] = dict(params)
        if json is not None:
            kwargs["json"] = json

        try:
            if method == "GET":
                response = await self._http.get(f"{self._base_url}{path}", **kwargs)
            else:
                response = await self._http.post(f"{self._base_url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise HackbotAPIError(f"Hackbot API request failed: {exc}") from exc

        if not response.is_success:
            detail = _error_detail(response)
            raise HackbotAPIError(
                f"Hackbot API returned HTTP {response.status_code} "
                f"({response.reason_phrase}) for {response.request.url}"
                + (f": {detail}" if detail else ""),
                status_code=response.status_code,
                detail=detail,
            )
        return response

    @staticmethod
    def _body(response: httpx.Response, what: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise HackbotInvalidResponseError(
                f"Hackbot API returned an invalid {what} "
                f"(HTTP {response.status_code}): the body is not JSON"
            ) from exc

    @staticmethod
    def _validate[M: BaseModel](
        model: type[M], body: Any, response: httpx.Response, what: str
    ) -> M:
        try:
            return model.model_validate(body)
        except ValidationError as exc:
            raise HackbotInvalidResponseError(
                f"Hackbot API returned an invalid {what} (HTTP {response.status_code})"
            ) from exc

    async def list_agents(
        self, *, on_behalf_of: str | None = None
    ) -> list[AgentDescriptor]:
        """Every agent the API can run, with its description and input schema."""
        response = await self._send("GET", "/agents", on_behalf_of=on_behalf_of)
        body = self._body(response, "agent list")
        if not isinstance(body, list):
            raise HackbotInvalidResponseError(
                f"Hackbot API returned an invalid agent list "
                f"(HTTP {response.status_code}): the body is not a list"
            )
        return [
            self._validate(AgentDescriptor, item, response, "agent list")
            for item in body
        ]

    async def get_run(
        self, run_id: str | UUID, *, on_behalf_of: str | None = None
    ) -> RunDoc:
        response = await self._send("GET", f"/runs/{run_id}", on_behalf_of=on_behalf_of)
        return self._validate(RunDoc, self._body(response, "run"), response, "run")

    async def list_runs(
        self,
        *,
        agent: str | None = None,
        status: RunStatus | str | None = None,
        requested_by: str | None = None,
        dedupe_key: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        on_behalf_of: str | None = None,
    ) -> list[RunDoc]:
        """Runs matching every filter given, newest first."""
        params = {
            name: value
            for name, value in {
                "agent": agent,
                "status": RunStatus(status).value if status is not None else None,
                "requested_by": requested_by,
                "dedupe_key": dedupe_key,
                "limit": limit,
                "offset": offset,
            }.items()
            if value is not None
        }
        response = await self._send(
            "GET", "/runs", on_behalf_of=on_behalf_of, params=params
        )
        body = self._body(response, "run list")
        if not isinstance(body, list):
            raise HackbotInvalidResponseError(
                f"Hackbot API returned an invalid run list "
                f"(HTTP {response.status_code}): the body is not a list"
            )
        return [self._validate(RunDoc, item, response, "run list") for item in body]

    async def list_actions(
        self, run_id: str | UUID, *, on_behalf_of: str | None = None
    ) -> ApplyActionsResponse:
        """A run's recorded actions and their apply state.

        Empty until the API records them, which it does once a succeeded run is
        processed (or on the first apply); until then, `summary.actions` on the
        run lists what the agent proposed.
        """
        response = await self._send(
            "GET", f"/runs/{run_id}/actions", on_behalf_of=on_behalf_of
        )
        return self._validate(
            ApplyActionsResponse,
            self._body(response, "action list"),
            response,
            "action list",
        )

    async def get_artifact_url(
        self, run_id: str | UUID, artifact: str, *, on_behalf_of: str | None = None
    ) -> str:
        """A short-lived signed URL to download one of a run's artifacts."""
        response = await self._send(
            "GET",
            f"/runs/{run_id}/artifacts/{quote(artifact.lstrip('/'))}",
            on_behalf_of=on_behalf_of,
        )
        body = self._body(response, "artifact URL")
        if not isinstance(body, dict) or not isinstance(body.get("url"), str):
            raise HackbotInvalidResponseError(
                f"Hackbot API returned an invalid artifact URL "
                f"(HTTP {response.status_code})"
            )
        return body["url"]

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
        params = {} if dedupe_key is None else {"dedupe_key": dedupe_key}
        response = await self._send(
            "POST",
            f"/agents/{agent_name}/runs",
            on_behalf_of=on_behalf_of,
            params=params,
            json=dict(inputs),
        )
        body = self._body(response, "run")

        if not isinstance(body, dict):
            raise HackbotInvalidResponseError(
                f"Hackbot API returned an invalid run (HTTP {response.status_code}): "
                "the body is not an object"
            )

        # The API distinguishes the two outcomes only by status code: `201` for the
        # run this request started, `200` for one a `dedupe_key` collapsed onto.
        try:
            return TriggeredRun.model_validate(
                {**body, "is_new": response.status_code == 201}
            )
        except ValidationError as exc:
            raise HackbotInvalidResponseError(
                f"Hackbot API returned an invalid run (HTTP {response.status_code})"
            ) from exc

    async def apply_actions(
        self, run_id: str | UUID, *, on_behalf_of: str | None = None
    ) -> ApplyActionsResponse:
        """Apply every one of a run's actions that has not landed yet.

        Idempotent, because the API skips rows already marked applied: calling
        this on a run whose actions all landed is a no-op.
        """
        response = await self._send(
            "POST", f"/runs/{run_id}/actions/apply", on_behalf_of=on_behalf_of
        )
        return self._validate(
            ApplyActionsResponse,
            self._body(response, "action list"),
            response,
            "action list",
        )
