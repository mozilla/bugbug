"""Authorization checks for Bugzilla webhook actors."""

import httpx
from cachetools import TTLCache

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
        self._cache: TTLCache[str, str | None] = TTLCache(
            maxsize=cache_maxsize,
            ttl=cache_ttl_seconds,
        )

    async def is_authorized(self, login: str) -> str | None:
        """Return the nickname of a login in the authorized group, else None."""
        login = login.lower()

        if login not in self._cache:
            self._cache[login] = await self._is_user_in_group(
                login, self._authorized_group_name
            )
        return self._cache[login]

    # TODO: Move this REST call to a shared Bugzilla client library (#6459).
    async def _is_user_in_group(self, login: str, group_name: str) -> str | None:
        """Return the nickname of a Bugzilla account in a group, else None."""
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{self._api_url}/user",
                params={
                    "names": login,
                    "groups": group_name,
                    "include_fields": "name,nick",
                    # Report an unknown login in ``faults`` instead of failing
                    # the request, so it maps to "not authorized", not a 500.
                    "permissive": "1",
                },
                headers={"X-Bugzilla-API-Key": self._api_key},
            )
        response.raise_for_status()
        users = response.json().get("users")
        return users[0]["nick"] if users else None
