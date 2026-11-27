"""The one place that talks to Bugzilla with a real credential.

The proxy holds two: a privileged key for scopes with a private grant, and an
unprivileged one for everything else. Each has its own client and its own
cache of what it has seen.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from cachetools import TTLCache

from bugzilla_proxy.config import Settings
from bugzilla_proxy.scope import AUTH_FIELDS, Scope

log = logging.getLogger(__name__)


class UpstreamError(Exception):
    """Bugzilla could not be reached, or answered with something unusable."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class Upstream:
    """A thin async client holding one upstream credential.

    Exposes GETs and nothing else, so no code path here can write to BMO even
    by mistake. One instance per credential, each handed only to the code path
    it serves, so a request without a private grant cannot reach the
    privileged key.
    """

    def __init__(self, settings: Settings, api_key: str) -> None:
        headers = {"X-Bugzilla-API-Key": api_key, "User-Agent": "bugzilla-proxy"}
        if settings.bugzilla_edge_key:
            headers["Mozilla-Edge-Key"] = settings.bugzilla_edge_key
        self._client = httpx.AsyncClient(
            base_url=settings.bugzilla_api_url.rstrip("/"),
            headers=headers,
            timeout=settings.bugzilla_timeout_seconds,
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

    async def open_stream(self, path: str, params: dict[str, Any]) -> httpx.Response:
        """Start a GET whose body is relayed unread; the caller must close it.

        For responses too large to hold in memory, such as attachment data,
        and only once the request has been authorized by other means.
        """
        request = self._client.build_request(
            "GET", f"/{path.lstrip('/')}", params=params
        )
        try:
            return await self._client.send(request, stream=True)
        except httpx.HTTPError as exc:
            log.warning("Upstream request to %s failed: %s", path, exc)
            raise UpstreamError("Bugzilla is unreachable") from exc

    async def aclose(self) -> None:
        await self._client.aclose()


PRIVILEGED = "privileged"
UNPRIVILEGED = "unprivileged"


def key_for(scope: Scope) -> str:
    """Which key reads for ``scope``: the unprivileged one unless it must not.

    A scope that cannot reach private bugs reads with a key from an account in
    no groups, so BMO refuses anything private even if a check here were
    wrong. Only a scope with a private grant needs, and gets, the privileged
    key, and stays behind every check the proxy applies.
    """
    return PRIVILEGED if scope.is_private else UNPRIVILEGED


class Backend:
    """One upstream credential, and what it has told us about each bug.

    Kept per credential: the two keys see different bugs, so an answer cached
    from one must never decide a request served with the other.
    """

    def __init__(self, upstream: Upstream, settings: Settings) -> None:
        self.upstream = upstream
        # Keyed by bug id, not by token: what this credential sees of a bug
        # is the same for every caller using it. The TTL bounds how long a
        # newly-private bug keeps being served.
        self.bug_cache: TTLCache = TTLCache(
            maxsize=settings.decision_cache_max_entries,
            ttl=settings.decision_cache_ttl_seconds,
        )

    async def auth_fields(self, bug_ids: list[int]) -> dict[int, dict[str, Any] | None]:
        """Fetch just enough of each bug to decide access, memoised.

        One upstream call covers every id not already cached. An id upstream
        does not return maps to None.
        """
        missing = sorted({i for i in bug_ids if i not in self.bug_cache})
        if missing:
            payload = await self.upstream.get(
                "bug",
                {
                    "id": ",".join(str(i) for i in missing),
                    "include_fields": ",".join(sorted(AUTH_FIELDS)),
                },
            )
            found: dict[int, dict[str, Any]] = {}
            for bug in payload.get("bugs") or []:
                try:
                    found[int(bug["id"])] = bug
                except KeyError, TypeError, ValueError:
                    continue
            for bug_id in missing:
                self.bug_cache[bug_id] = found.get(bug_id)
        return {bug_id: self.bug_cache.get(bug_id) for bug_id in bug_ids}


def build_backends(
    settings: Settings,
    privileged_upstream: Upstream | None = None,
    unprivileged_upstream: Upstream | None = None,
) -> dict[str, Backend | None]:
    """One backend per key, keyed by name. Either may be missing.

    A token needing a missing one gets a 503 rather than the other key.
    """
    if (
        settings.bugzilla_privileged_api_key
        and settings.bugzilla_privileged_api_key
        == settings.bugzilla_unprivileged_api_key
    ):
        raise ValueError(
            "the privileged and unprivileged Bugzilla API keys are the same; "
            "scopes without private grants must read with a key from an "
            "account in no groups, so a bug in the proxy cannot reach private "
            "data"
        )

    def backend(given: Upstream | None, key: str) -> Backend | None:
        upstream = given or (Upstream(settings, key) if key else None)
        return Backend(upstream, settings) if upstream is not None else None

    return {
        PRIVILEGED: backend(privileged_upstream, settings.bugzilla_privileged_api_key),
        UNPRIVILEGED: backend(
            unprivileged_upstream, settings.bugzilla_unprivileged_api_key
        ),
    }
