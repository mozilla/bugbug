"""Read-only Bugzilla MCP broker.

Sidecar that holds the Bugzilla API key and serves the `bugzilla` MCP tools over
`/bugzilla/mcp`, so the agent container binds no credentials.
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

log = logging.getLogger("perftest-repair-broker")


class BrokerInputs(BaseSettings):
    bugzilla_api_url: str
    bugzilla_api_key: str
    host: str = "0.0.0.0"
    port: int = 8765

    model_config = SettingsConfigDict(
        extra="ignore",
    )


def _mcp_endpoint(manager: StreamableHTTPSessionManager):
    """An ASGI app serving one MCP server over streamable HTTP."""

    async def handler(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    return handler


def build_app(inputs: BrokerInputs) -> Starlette:
    client = bugsy.Bugsy(
        api_key=inputs.bugzilla_api_key, bugzilla_url=inputs.bugzilla_api_url
    )
    bugzilla_config = build_sdk_server(
        "bugzilla", BugzillaContext(client=client), bugzilla.TOOLS
    )
    bugzilla_manager = StreamableHTTPSessionManager(
        app=bugzilla_config["instance"], stateless=True
    )

    @asynccontextmanager
    async def lifespan(app):
        async with bugzilla_manager.run():
            log.info(
                "broker ready on %s:%d (bugzilla read-only)",
                inputs.host,
                inputs.port,
            )
            yield

    return Starlette(
        routes=[
            Mount("/bugzilla/mcp", app=_mcp_endpoint(bugzilla_manager)),
        ],
        lifespan=lifespan,
    )


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
