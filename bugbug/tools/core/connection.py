# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Core connection utilities for bugbug tools."""

import os
from functools import cache

import httpx


def get_user_agent() -> str:
    """Get the User-Agent string from environment or default."""
    return os.getenv("USER_AGENT", "bugbug")


@cache
def get_http_client() -> httpx.AsyncClient:
    """Get the shared HTTP client instance."""
    http_client = httpx.AsyncClient(
        follow_redirects=True,
        headers={
            "User-Agent": get_user_agent(),
        },
    )

    return http_client


async def close_http_client() -> None:
    """Close the shared HTTP client instance and clear the cache."""
    if get_http_client.cache_info().currsize == 0:
        # No cached client to close
        return

    client = get_http_client()
    get_http_client.cache_clear()
    await client.aclose()
