"""Bugzilla MCP broker.

Sidecar container that holds the Bugzilla API key and serves the
bugzilla MCP tools over HTTP. The agent process (in a sibling container
in the same Cloud Run Job task) reaches us at `127.0.0.1:<port>/mcp`.
The agent container itself binds no Bugzilla credentials.
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
    ctx = BugzillaContext(client=client)
    sdk_config = build_sdk_server("bugzilla", ctx, bugzilla.TOOLS)
    mcp_server = sdk_config["instance"]

    manager = StreamableHTTPSessionManager(app=mcp_server, stateless=True)

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            log.info(
                "bugzilla broker ready on %s:%d (read-only)",
                inputs.host,
                inputs.port,
            )
            yield

    async def mcp_handler(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    return Starlette(routes=[Mount("/mcp", app=mcp_handler)], lifespan=lifespan)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    inputs = BrokerInputs()
    app = build_app(inputs)
    uvicorn.run(app, host=inputs.host, port=inputs.port, log_config=None)


if __name__ == "__main__":
    main()
