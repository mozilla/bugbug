# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Core connection utilities for bugbug tools."""

import logging
import os
from functools import cache
from typing import Callable

import httpx
import tenacity

logger = logging.getLogger(__name__)

# Longest wait honored from a Retry-After header, so a single call stays bounded.
MAX_RETRY_AFTER = 30

# Connecting is quick unless something is wrong, and failures are retried. Reads
# get more time: Conduit can take tens of seconds on large revisions, GitHub
# serves large files slowly, and the timers keep running while the event loop
# is busy with other concurrent reviews.
HTTP_TIMEOUT = httpx.Timeout(60, connect=10)


def get_user_agent() -> str:
    """Get the User-Agent string from environment or default."""
    return os.getenv("USER_AGENT", "bugbug")


# Hosts the GitHub token authenticates to. raw.githubusercontent.com is included
# because unauthenticated raw file downloads are rate limited much more strictly.
GITHUB_HOSTS = frozenset({"api.github.com", "raw.githubusercontent.com"})


class GitHubTokenAuth(httpx.Auth):
    """Authenticate requests to GitHub hosts with a token, and only those."""

    def __init__(self, token: str) -> None:
        self.token = token

    def auth_flow(self, request: httpx.Request):
        if request.url.host in GITHUB_HOSTS:
            request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


def is_transient_http_error(exc: BaseException) -> bool:
    """Return whether a failed HTTP request may succeed if retried."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, httpx.TransportError)


_backoff = tenacity.wait_exponential_jitter(initial=1, max=10)


def _wait_before_retry(retry_state: tenacity.RetryCallState) -> float:
    """Wait as long as a Retry-After header asks, or back off exponentially."""
    assert retry_state.outcome is not None
    exc = retry_state.outcome.exception()
    if isinstance(exc, httpx.HTTPStatusError):
        retry_after = exc.response.headers.get("Retry-After", "")
        if retry_after.isdigit():
            return min(int(retry_after), MAX_RETRY_AFTER)
    return _backoff(retry_state)


def retry_transient_errors(
    is_transient: Callable[[BaseException], bool] = is_transient_http_error,
):
    """Retry the decorated function when it fails with a transient error.

    Only errors for which is_transient returns True are retried, at most three
    times, with an exponential backoff of up to 10 seconds between attempts.
    """
    return tenacity.retry(
        stop=tenacity.stop_after_attempt(4),
        wait=_wait_before_retry,
        retry=tenacity.retry_if_exception(is_transient),
        before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


@cache
def get_http_client() -> httpx.AsyncClient:
    """Get the shared HTTP client instance."""
    token = os.getenv("GITHUB_TOKEN")
    http_client = httpx.AsyncClient(
        auth=GitHubTokenAuth(token) if token else None,
        follow_redirects=True,
        timeout=HTTP_TIMEOUT,
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
