"""Prepare Android browsers before forwarding calls to their DevTools MCP servers.

The existing MCP servers connect to the browsers' debugging endpoints, but do
not handle our Android app setup or foreground switching. This proxy starts or
activates the appropriate browser before each tool call, installs any extensions
requested by the task, and forwards the call to the existing server. It also
restarts Firefox after a reset so a Puppeteer script can take over its session.

Keeping this preparation here lets the agent use the existing browser tools
without having to issue separate Android setup commands between tool calls.
Callers launch this module with a browser identifier, extension slugs, and the
upstream MCP server command.
"""

import asyncio
import base64
import contextlib
import json
import logging
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server import Server
from mcp.server.stdio import stdio_server

from autowebcompat_tools.amo import download, extract_extension_id, resolve_xpi_url
from autowebcompat_tools.android_browser import AndroidBrowser

logger = logging.getLogger(__name__)


async def install_extension(session, slug):
    """Install a signed AMO addon using the upstream MCP connection."""

    def fetch():
        with tempfile.TemporaryDirectory(prefix="android-addon-") as tmp:
            url, version = resolve_xpi_url(slug)
            path = Path(tmp) / "addon.xpi"
            download(url, path)
            return (
                version,
                extract_extension_id(path),
                base64.b64encode(path.read_bytes()).decode("ascii"),
            )

    version, extension_id, encoded = await asyncio.to_thread(fetch)
    logger.info("Installing Android extension %s %s through Firefox MCP", slug, version)
    result = await session.call_tool(
        "install_extension",
        {
            "type": "base64",
            "value": encoded,
            "permanent": True,
        },
    )
    text = "\n".join(block.text for block in result.content if block.type == "text")
    if result.isError or f"ID: {extension_id}" not in text:
        raise RuntimeError(f"Android extension installation failed: {text}")


class AndroidTools:
    def __init__(self, browser, session, extensions=()):
        self.browser = browser
        self.session = session
        self.extensions = extensions
        self.started = False
        self.installed = False
        self.lock = asyncio.Lock()

    async def call(self, name, arguments):
        async with self.lock:
            with contextlib.redirect_stdout(sys.stderr):
                if self.started:
                    await asyncio.to_thread(self.browser.activate)
                else:
                    await asyncio.to_thread(self.browser.start, fresh=True)
            self.started = True
            if not self.installed:
                for slug in self.extensions:
                    await install_extension(self.session, slug)
                self.installed = True
            result = await self.session.call_tool(name, arguments)
            if name == "restart_firefox":
                self.started = False
                with contextlib.redirect_stdout(sys.stderr):
                    await asyncio.to_thread(self.browser.start)
                self.started = True
            return result


async def serve(browser, command, extensions=()):
    parameters = StdioServerParameters(command=command[0], args=command[1:])
    async with (
        stdio_client(parameters) as (reader, writer),
        ClientSession(
            reader, writer, read_timeout_seconds=timedelta(seconds=180)
        ) as session,
    ):
        await session.initialize()
        tools = AndroidTools(browser, session, extensions)
        server = Server("android-devtools")

        @server.list_tools()
        async def list_tools():
            return (await session.list_tools()).tools

        @server.call_tool()
        async def call_tool(name, arguments):
            return await tools.call(name, arguments)

        async with stdio_server() as (incoming, outgoing):
            await server.run(incoming, outgoing, server.create_initialization_options())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    marker, encoded_extensions, *command = sys.argv[1:]
    asyncio.run(
        serve(AndroidBrowser.parse(marker), command, json.loads(encoded_extensions))
    )
