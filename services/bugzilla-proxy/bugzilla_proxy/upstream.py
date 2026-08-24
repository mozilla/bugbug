"""The one place that talks to Bugzilla with a real credential."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from bugzilla_proxy.config import Settings

log = logging.getLogger(__name__)


class UpstreamError(Exception):
    """Bugzilla could not be reached, or answered with something unusable."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class Upstream:
    """A thin async client holding the upstream credential.

    Exposes one GET and nothing else, so no code path here can write to BMO
    even by mistake.
    """

    def __init__(self, settings: Settings) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.upstream_url.rstrip("/"),
            headers={
                "X-Bugzilla-API-Key": settings.upstream_api_key,
                "User-Agent": "bugzilla-proxy",
            },
            timeout=settings.upstream_timeout_seconds,
        )

    async def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.get(f"/{path.lstrip('/')}", params=params)
        except httpx.HTTPError as exc:
            log.warning("Upstream request to %s failed: %s", path, exc)
            raise UpstreamError("Bugzilla is unreachable") from exc

        if response.status_code >= 500:
            raise UpstreamError("Bugzilla returned a server error")

        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamError("Bugzilla returned a non-JSON response") from exc

        if not isinstance(payload, dict):
            raise UpstreamError("Bugzilla returned an unexpected response shape")

        if response.status_code >= 400 or payload.get("error"):
            # Pass Bugzilla's own complaint through rather than inventing one,
            # but never its code: ours mean something different.
            message = str(payload.get("message") or "Bugzilla rejected the request")
            raise UpstreamError(message, status_code=response.status_code)

        return payload

    async def aclose(self) -> None:
        await self._client.aclose()
