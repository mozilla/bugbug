"""Sentry MCP broker.

Sidecar container that holds privileged API keys and serves Sentry MCP tools
over HTTP to the agent process (a sibling container in the same Cloud Run Job
task), which reaches us at `127.0.0.1:<port>`. The agent container itself binds
no credentials:

- Sentry: the `Sentry` MCP tools over `/mcp` (read-only, live during the run).
"""

import httpx
import logging
from contextlib import asynccontextmanager

import uvicorn
from agent_tools import sentry
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.sentry import SentryContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.applications import Starlette
from starlette.routing import Mount

log = logging.getLogger("sentry-broker")


class BrokerInputs(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8765
    sentry_api_key: str
    sentry_api_url_base: str
    sentry_org_name: str

    model_config = SettingsConfigDict(extra="ignore")


def build_app(inputs: BrokerInputs) -> Starlette:
    ctx = SentryContext(
        api_token=inputs.sentry_api_key,
        api_url_base=inputs.sentry_api_url_base,
        client=httpx.AsyncClient(),
        org_name=inputs.sentry_org_name,
    )

    sdk_config = build_sdk_server("sentry", ctx, sentry.TOOLS)
    mcp_server = sdk_config["instance"]

    manager = StreamableHTTPSessionManager(app=mcp_server, stateless=True)

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            log.info(
                "broker ready on %s:%d (sentry read-only)",
                inputs.host,
                inputs.port,
            )
            yield

    async def mcp_handler(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    return Starlette(
        routes=[
            Mount("/mcp", app=mcp_handler),
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
