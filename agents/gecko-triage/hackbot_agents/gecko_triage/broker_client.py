"""The agent's side of the broker: one MCP session, with a time limit."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

Call = Callable[[str, dict], Awaitable[dict]]


@asynccontextmanager
async def broker_session(url: str, timeout: float) -> AsyncIterator[Call]:
    """A ``call(tool, args)`` on the broker at ``url``, over one MCP session.

    ``call`` returns the parsed JSON reply. It raises RuntimeError when the broker
    reports an error. The session takes calls in parallel. The connection and all the
    calls in the block must end in ``timeout`` seconds, else TimeoutError.
    """
    async with (
        asyncio.timeout(timeout),
        streamablehttp_client(url) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        async def call(tool: str, args: dict) -> dict:
            res = await session.call_tool(tool, args)
            if res.isError or not res.content:
                raise RuntimeError(
                    "".join(getattr(c, "text", "") for c in res.content) or "no result"
                )
            return json.loads(res.content[0].text)

        yield call
