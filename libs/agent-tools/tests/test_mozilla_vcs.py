# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Shared VCS reads retain tool limits and support full evidence downloads."""

import httpx
import pytest
from agent_tools.mozilla_vcs import MozillaVcsContext, get_commit_diff
from agent_tools.registry import ToolError


@pytest.mark.parametrize("full_patch", [False, True])
async def test_diff_limit_with_caller_owned_client(full_patch):
    patch = "+source line\n" * 10_000
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=patch))
    ) as client:
        ctx = MozillaVcsContext(client=client)
        if full_patch:
            ctx.max_diff_bytes = None
        result = await get_commit_diff(ctx, "a" * 40)
        assert not client.is_closed
    assert result["truncated"] is not full_patch
    assert result["diff"] == (patch if full_patch else patch[:60_000])


@pytest.mark.parametrize("status", [404, 500])
async def test_shared_client_errors_remain_tool_errors(status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status))
    ) as client:
        with pytest.raises(ToolError) as exc:
            await get_commit_diff(MozillaVcsContext(client=client), "a" * 40)
    assert exc.value.payload["error"] == (
        "not_found" if status == 404 else "http_status"
    )
