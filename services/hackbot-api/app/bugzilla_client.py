"""Minimal async client for Bugzilla's REST API.

TODO: Replace with a shared ``bugzilla-client`` workspace lib (the
``phabricator-client`` treatment) once one exists.
"""

from __future__ import annotations

import httpx

_REQUEST_TIMEOUT_SECONDS = 30


class BugzillaUserClient:
    """Minimal async client for Bugzilla's user lookup endpoint."""

    def __init__(self, url: str) -> None:
        self._rest_url = url.rstrip("/") + "/rest"

    async def is_user_in_group(self, login: str, group_id: int) -> bool:
        """Return whether a Bugzilla account exists and belongs to a group.

        ``group_ids`` filters server-side: the account appears in ``users``
        only when it exists and is a member. The filter needs no API key,
        which keeps this internet-facing service free of privileged Bugzilla
        credentials (reading another account's ``groups`` directly would
        require one). BMO rejects an unknown ``group_ids`` value outright, so
        the filter cannot be silently ignored.
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
