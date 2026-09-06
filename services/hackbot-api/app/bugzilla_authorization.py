"""Authorization checks for Bugzilla webhook actors."""

from __future__ import annotations

import asyncio

import httpx
from cachetools import TTLCache

AUTHORIZED_GROUP_ID = 9  # bmo-editbugs-team

_REQUEST_TIMEOUT_SECONDS = 30


class BugzillaAuthorizer:
    """Cache-backed per-user authorization checks against a Bugzilla group."""

    def __init__(
        self,
        url: str,
        authorized_group_id: int,
        *,
        cache_ttl_seconds: int = 300,
        cache_maxsize: int = 4096,
    ) -> None:
        self._rest_url = url.rstrip("/") + "/rest"
        self._authorized_group_id = authorized_group_id
        self._cache: TTLCache[str, bool] = TTLCache(
            maxsize=cache_maxsize,
            ttl=cache_ttl_seconds,
        )
        self._lock = asyncio.Lock()

    async def is_authorized(self, login: str) -> bool:
        """Return whether a Bugzilla login belongs to the authorized group."""
        login = login.lower()

        cached = self._cache.get(login)
        if cached is not None:
            return cached

        async with self._lock:
            cached = self._cache.get(login)
            if cached is not None:
                return cached

            authorized = await self._is_user_in_group(login, self._authorized_group_id)
            self._cache[login] = authorized
            return authorized

    # TODO: Move this REST call to a shared Bugzilla client library (#6459).
    async def _is_user_in_group(self, login: str, group_id: int) -> bool:
        """Return whether a Bugzilla account exists and belongs to a group.

        The ``group_ids`` parameter filters server-side and needs no API key,
        so the service holds no Bugzilla credential.
        """
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{self._rest_url}/user",
                params={
                    "names": login,
                    "group_ids": str(group_id),
                    "include_fields": "name",
                    # Report an unknown login in ``faults`` instead of failing
                    # the request, so it maps to "not authorized", not a 500.
                    "permissive": "1",
                },
            )
        response.raise_for_status()
        return bool(response.json().get("users"))
