"""Authorization checks for Bugzilla webhook actors."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from cachetools import TTLCache

if TYPE_CHECKING:
    from app.bugzilla_client import BugzillaUserClient

AUTHORIZED_GROUP_ID = 9  # bmo-editbugs-team


class BugzillaAuthorizer:
    """Cache-backed per-user authorization checks against a Bugzilla group."""

    def __init__(
        self,
        client: BugzillaUserClient,
        authorized_group_id: int,
        *,
        cache_ttl_seconds: int = 300,
        cache_maxsize: int = 4096,
    ) -> None:
        self._client = client
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

            authorized = await self._client.is_user_in_group(
                login, self._authorized_group_id
            )
            self._cache[login] = authorized
            return authorized
