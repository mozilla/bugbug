# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from unittest.mock import AsyncMock

import httpx
import pytest

from bugbug.tools.core.connection import (
    is_transient_http_error,
    retry_transient_errors,
)

URL = "https://example.com/"


def status_error(status: int, headers: dict[str, str] | None = None):
    request = httpx.Request("GET", URL)
    response = httpx.Response(status, headers=headers, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


@pytest.mark.parametrize(
    "exc, expected",
    [
        (status_error(429), True),
        (status_error(500), True),
        (status_error(503), True),
        (status_error(400), False),
        (status_error(403), False),
        (status_error(404), False),
        (httpx.ConnectTimeout(""), True),
        (httpx.ReadTimeout(""), True),
        (httpx.ConnectError("boom"), True),
        (ValueError("boom"), False),
    ],
)
def test_is_transient_http_error(exc, expected) -> None:
    assert is_transient_http_error(exc) is expected


def make_retried(*outcomes):
    calls = AsyncMock(side_effect=outcomes)

    @retry_transient_errors()
    async def fetch():
        return await calls()

    sleep = AsyncMock()
    fetch.retry.sleep = sleep  # type: ignore[attr-defined]
    return fetch, calls, sleep


@pytest.mark.asyncio
async def test_retry_recovers_from_transient_errors() -> None:
    fetch, calls, sleep = make_retried(
        httpx.ConnectTimeout(""), status_error(502), "ok"
    )
    assert await fetch() == "ok"
    assert calls.await_count == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_retry_gives_up_after_four_attempts() -> None:
    fetch, calls, _ = make_retried(*[status_error(503)] * 5)
    with pytest.raises(httpx.HTTPStatusError):
        await fetch()
    assert calls.await_count == 4


@pytest.mark.asyncio
async def test_retry_does_not_retry_client_errors() -> None:
    fetch, calls, sleep = make_retried(status_error(404), "ok")
    with pytest.raises(httpx.HTTPStatusError):
        await fetch()
    assert calls.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after, expected", [("7", 7), ("3600", 30)])
async def test_retry_honors_retry_after(retry_after, expected) -> None:
    fetch, _, sleep = make_retried(
        status_error(429, {"Retry-After": retry_after}), "ok"
    )
    assert await fetch() == "ok"
    sleep.assert_awaited_once_with(expected)
