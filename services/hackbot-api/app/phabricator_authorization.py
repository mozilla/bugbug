"""Authorization checks for Phabricator webhook authors."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from cachetools import TTLCache

if TYPE_CHECKING:
    from phabricator_client import PhabricatorClient


class PhabricatorAuthorizer:
    """Cache-backed authorization checks against a Phabricator project."""

    def __init__(
        self,
        group_phid: str,
        *,
        cache_ttl_seconds: int = 300,
        missing_member_refresh_cooldown_seconds: int = 30,
    ) -> None:
        self._group_phid = group_phid
        self._members_cache: TTLCache[str, frozenset[str]] = TTLCache(
            maxsize=1,
            ttl=cache_ttl_seconds,
        )
        self._members_lock = asyncio.Lock()
        self._last_members_refresh = 0.0
        self._missing_member_refresh_cooldown_seconds = (
            missing_member_refresh_cooldown_seconds
        )

    async def is_authorized(self, client: PhabricatorClient, author_phid: str) -> bool:
        """Return whether an author belongs to the authorized project.

        Known members use the cached project snapshot. An unknown author causes
        one refresh so recently added members take effect promptly. Subsequent
        unknown authors use a short cooldown to avoid a Phabricator request for
        every unauthorized webhook delivery.
        """
        cached_members = self._members_cache.get(self._group_phid)
        if cached_members is not None and author_phid in cached_members:
            return True

        async with self._members_lock:
            cached_members = self._members_cache.get(self._group_phid)
            if cached_members is not None and author_phid in cached_members:
                return True

            now = time.monotonic()
            if (
                cached_members is not None
                and now - self._last_members_refresh
                < self._missing_member_refresh_cooldown_seconds
            ):
                return False

            members = await client.get_project_members(self._group_phid)
            self._members_cache[self._group_phid] = members
            self._last_members_refresh = time.monotonic()
            return author_phid in members


# Members of this project are authorized to trigger Hackbot.
AUTHORIZED_GROUP_PHID = "PHID-PROJ-njo5uuqyyq3oijbkhy55"  # bmo-editbugs-team
phabricator_authorizer = PhabricatorAuthorizer(AUTHORIZED_GROUP_PHID)
