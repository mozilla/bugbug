"""Bugzilla MCP broker.

Sidecar container that holds the Bugzilla API key and serves the agent-tools Bugzilla
tools over HTTP. The agent process, in a sibling container of the same Cloud Run Job
task, reaches it at `127.0.0.1:<port>/mcp`. The agent container itself binds no
Bugzilla credentials.

The bot account reads only public bugs, so the broker does not filter what it serves.
`download_attachment` is left out: it would write the file inside this container. The
agent reads an attachment with its own `fetch_attachment` tool.
"""

import logging
from contextlib import asynccontextmanager

import bugsy
import uvicorn
from agent_tools import bugzilla
from agent_tools.bugzilla import BugzillaContext
from agent_tools.claude_sdk import build_sdk_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.applications import Starlette
from starlette.routing import Mount

log = logging.getLogger("bugzilla-broker")

# Seconds before a Bugzilla request fails, so a request that hangs ends.
BUGZILLA_TIMEOUT = 60
TOOLS = [t for t in bugzilla.TOOLS if t.name != "download_attachment"]


class BrokerInputs(BaseSettings):
    bugzilla_api_url: str
    bugzilla_api_key: str
    host: str = "0.0.0.0"
    port: int = 8765

    model_config = SettingsConfigDict(extra="ignore")


def build_app(inputs: BrokerInputs) -> Starlette:
    client = bugsy.Bugsy(
        api_key=inputs.bugzilla_api_key, bugzilla_url=inputs.bugzilla_api_url
    )
    ctx = BugzillaContext(client=client, timeout=BUGZILLA_TIMEOUT)
    sdk_config = build_sdk_server("bugzilla", ctx, TOOLS)
    manager = StreamableHTTPSessionManager(app=sdk_config["instance"], stateless=True)

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            log.info(
                "bugzilla broker ready on %s:%d (read-only)", inputs.host, inputs.port
            )
            yield

    return Starlette(
        routes=[Mount("/mcp", app=manager.handle_request)], lifespan=lifespan
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    inputs = BrokerInputs()
    uvicorn.run(build_app(inputs), host=inputs.host, port=inputs.port, log_config=None)


if __name__ == "__main__":
    main()
