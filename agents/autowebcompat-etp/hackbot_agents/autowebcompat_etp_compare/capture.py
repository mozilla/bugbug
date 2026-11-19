"""Load the site in each configuration and screenshot it, without the model.

Drives the same Firefox DevTools MCP server the model would, through a plain
MCP client, so the screenshots and content blocking logs match what the model
sees if it is called in later.
"""

import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, get_args

from claude_agent_sdk.types import McpStdioServerConfig
from hackbot_runtime import AgentError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .result import ETP_MODES, Configuration

logger = logging.getLogger("autowebcompat-etp-compare")

# set_etp_mode can wait minutes for list downloads.
TOOL_TIMEOUT = timedelta(minutes=5)

# How long to wait for the load event, and after it for late scripts, before
# the screenshot. A longer settle time lets more of the page reach its final
# state, so fewer reload variations reach the model.
LOAD_TIMEOUT_SECONDS = 30
SETTLE_SECONDS = 5

BLOCKED_TRACKERS_RE = re.compile(r"^Blocked trackers: (\d+)(?: \((.*)\))?$", re.M)


class ToolError(Exception):
    pass


@dataclass
class Capture:
    configuration: Configuration
    screenshot: Path
    blocked_trackers: int
    blocked_tracker_origins: list[str]
    # What wait_for_page_load reported, or the navigation error.
    load_status: str
    navigation_failed: bool = False
    full_page: bool = True
    # What screenshot_full_page said about the stitching.
    screenshot_note: str = ""


class DevtoolsClient:
    def __init__(self, session: ClientSession):
        self.session = session

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        result = await self.session.call_tool(
            name, arguments or {}, read_timeout_seconds=TOOL_TIMEOUT
        )
        text = "\n".join(
            item.text for item in result.content if item.type == "text"
        ).strip()
        if result.isError:
            raise ToolError(f"{name} failed: {text}")
        return text


@asynccontextmanager
async def devtools_client(
    server: McpStdioServerConfig,
) -> AsyncIterator[DevtoolsClient]:
    """Start the MCP server (and with it Firefox) and connect to it."""
    params = StdioServerParameters(
        command=server["command"],
        args=server.get("args", []),
        # The full environment, as the Claude CLI passes it: Firefox needs
        # DISPLAY, which the MCP default environment leaves out.
        env={**os.environ, **server.get("env", {})},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield DevtoolsClient(session)


def parse_blocked_trackers(log: str) -> tuple[int, list[str]]:
    """The count and origins get_content_blocking_log's response starts with."""
    match = BLOCKED_TRACKERS_RE.search(log)
    if match is None:
        raise AgentError(f"unexpected get_content_blocking_log response: {log[:500]}")
    origins = match.group(2).split(", ") if match.group(2) else []
    return int(match.group(1)), origins


async def capture(
    client: DevtoolsClient, configuration: Configuration, url: str, path: Path
) -> Capture:
    """Load `url` in `configuration` as a first visit and screenshot all of it."""
    try:
        await client.call("set_etp_mode", {"mode": ETP_MODES[configuration]})
    except ToolError as exc:
        raise AgentError(
            f"could not set up the {configuration} configuration: {exc}"
        ) from exc

    navigation_failed = False
    try:
        await client.call("navigate_page", {"url": url})
        load_status = await client.call(
            "wait_for_page_load",
            {"timeoutSeconds": LOAD_TIMEOUT_SECONDS, "settleSeconds": SETTLE_SECONDS},
        )
    except ToolError as exc:
        # Still screenshot whatever Firefox shows (e.g. its error page).
        logger.warning("%s: %s", configuration, exc)
        load_status = str(exc)
        navigation_failed = True

    full_page = True
    try:
        screenshot_note = await client.call(
            "screenshot_full_page", {"saveTo": str(path)}
        )
        logger.info("%s: %s", configuration, screenshot_note)
    except ToolError as exc:
        # Capturing a very tall document can fail; the viewport still shows
        # whether the page rendered.
        logger.warning("%s: %s; taking a viewport screenshot", configuration, exc)
        screenshot_note = await client.call("screenshot_page", {"saveTo": str(path)})
        full_page = False

    count, origins = parse_blocked_trackers(
        await client.call("get_content_blocking_log")
    )
    logger.info("%s: %d blocked trackers", configuration, count)
    return Capture(
        configuration=configuration,
        screenshot=path,
        blocked_trackers=count,
        blocked_tracker_origins=origins,
        load_status=load_status,
        navigation_failed=navigation_failed,
        full_page=full_page,
        screenshot_note=screenshot_note,
    )


async def capture_configurations(
    server: McpStdioServerConfig, url: str, screenshot_dir: Path
) -> dict[Configuration, Capture]:
    """Capture the site in every configuration, in one Firefox session."""
    captures = {}
    async with devtools_client(server) as client:
        for configuration in get_args(Configuration):
            captures[configuration] = await capture(
                client, configuration, url, screenshot_dir / f"{configuration}.png"
            )
    return captures
