"""Authorization checks for Bugzilla webhook actors."""

from __future__ import annotations

import httpx
from cachetools import TTLCache

AUTHORIZED_GROUP_NAME = "editbugs"

_REQUEST_TIMEOUT_SECONDS = 30


class BugzillaAuthorizer:
    """Cache-backed per-user authorization checks against a Bugzilla group."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        authorized_group_name: str,
        *,
        cache_ttl_seconds: int = 300,
        cache_maxsize: int = 4096,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._authorized_group_name = authorized_group_name
        self._cache: TTLCache[str, bool] = TTLCache(
            maxsize=cache_maxsize,
            ttl=cache_ttl_seconds,
        )

    async def is_authorized(self, login: str) -> bool:
        """Return whether a Bugzilla login belongs to the authorized group."""
        login = login.lower()

        cached = self._cache.get(login)
        if cached is not None:
            return cached

        authorized = await self._is_user_in_group(login, self._authorized_group_name)
        self._cache[login] = authorized
        return authorized

    # TODO: Move this REST call to a shared Bugzilla client library (#6459).
    async def _is_user_in_group(self, login: str, group_name: str) -> bool:
        """Return whether a Bugzilla account exists and belongs to a group."""
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{self._api_url}/user",
                params={
                    "names": login,
                    "groups": group_name,
                    "include_fields": "name",
                    # Report an unknown login in ``faults`` instead of failing
                    # the request, so it maps to "not authorized", not a 500.
                    "permissive": "1",
                },
                headers={"X-Bugzilla-API-Key": self._api_key},
            )
        response.raise_for_status()
        return bool(response.json().get("users"))
