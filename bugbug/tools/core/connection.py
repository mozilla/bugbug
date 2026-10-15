# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Core connection utilities for bugbug tools."""

import json
import os
from functools import cache
from logging import getLogger

import httpx

logger = getLogger(__name__)


def raise_for_github_api_error(response: httpx.Response) -> None:
    """Log GitHub's error diagnostics without recording request credentials."""
    if response.status_code >= 400:
        authorization = response.request.headers.get("Authorization", "")

        def redact(value: str) -> str:
            for secret in (authorization, authorization.removeprefix("Bearer ")):
                if secret:
                    value = value.replace(secret, "[REDACTED]")
            return value[:1000]

        try:
            body = response.json()
            message = body.get("message") if isinstance(body, dict) else None
        except ValueError:
            message = None

        headers = (
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "X-RateLimit-Resource",
            "Retry-After",
            "X-Accepted-GitHub-Permissions",
            "X-GitHub-SSO",
            "X-GitHub-Request-Id",
        )
        logger.warning(
            "GitHub API error: %s",
            json.dumps(
                {
                    "url": str(response.request.url.copy_with(query=None)),
                    "status": response.status_code,
                    "authenticated": bool(authorization),
                    "message": redact(str(message)) if message is not None else None,
                    "headers": {
                        name: redact(response.headers[name])
                        for name in headers
                        if name in response.headers
                    },
                }
            ),
        )
    response.raise_for_status()


def get_user_agent() -> str:
    """Get the User-Agent string from environment or default."""
    return os.getenv("USER_AGENT", "bugbug")


@cache
def get_github_api_headers() -> dict[str, str]:
    """Return headers authenticating GitHub API requests, if a token is configured."""
    token = os.getenv("GITHUB_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


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
