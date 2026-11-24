"""Bugzilla and Phabricator broker for the uplift agent.

Sidecar holding the Bugzilla and Phabricator API keys, reached by the agent over
loopback in the same Cloud Run Job task. The agent container binds no credential:

- Bugzilla: the read-only `bugzilla` MCP tools over `/bugzilla/mcp`, so the model
  can read the originating bug.
- Phabricator: the read-only `phabricator` MCP tools over `/phabricator/mcp`, so
  the model can read each source revision's title, summary and comments.
- Phabricator: `/phabricator/api` mounts a read-only Conduit proxy
  (``phabricator_proxy``), which agent code fetches each source's diff through
  before the prompt is rendered. It allow-lists read methods and substitutes the
  real key.
"""

import logging
from contextlib import asynccontextmanager

import bugsy
import uvicorn
from agent_tools import bugzilla
from agent_tools import phabricator as phabricator_tools
from agent_tools.bugzilla import BugzillaContext
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.phabricator import PhabricatorContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from phabricator_client import PhabricatorClient, PhabricatorSettings
from phabricator_proxy import create_app as conduit_proxy
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.applications import Starlette
from starlette.routing import Mount

log = logging.getLogger("uplift-resolve-broker")


class BrokerInputs(BaseSettings):
    bugzilla_api_url: str
    bugzilla_api_key: str
    phabricator: PhabricatorSettings
    host: str = "0.0.0.0"
    port: int = 8765

    model_config = SettingsConfigDict(
        extra="ignore",
        env_nested_delimiter="_",
        env_nested_max_split=1,
    )


def mcp_endpoint(manager: StreamableHTTPSessionManager):
    """An ASGI app serving one MCP server over streamable HTTP."""

    async def handler(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    return handler


def build_app(inputs: BrokerInputs) -> Starlette:
    bugzilla_client = bugsy.Bugsy(
        api_key=inputs.bugzilla_api_key, bugzilla_url=inputs.bugzilla_api_url
    )
    bugzilla_config = build_sdk_server(
        "bugzilla", BugzillaContext(client=bugzilla_client), bugzilla.TOOLS
    )
    bugzilla_manager = StreamableHTTPSessionManager(
        app=bugzilla_config["instance"], stateless=True
    )

    phabricator_client = PhabricatorClient(inputs.phabricator)
    phabricator_config = build_sdk_server(
        "phabricator",
        PhabricatorContext(client=phabricator_client),
        phabricator_tools.TOOLS,
    )
    phabricator_manager = StreamableHTTPSessionManager(
        app=phabricator_config["instance"], stateless=True
    )

    @asynccontextmanager
    async def lifespan(app):
        async with bugzilla_manager.run(), phabricator_manager.run():
            log.info(
                "broker ready on %s:%d (bugzilla + phabricator read-only, "
                "phabricator conduit proxy)",
                inputs.host,
                inputs.port,
            )
            yield

    return Starlette(
        routes=[
            Mount("/bugzilla/mcp", app=mcp_endpoint(bugzilla_manager)),
            Mount("/phabricator/mcp", app=mcp_endpoint(phabricator_manager)),
            Mount("/phabricator/api", app=conduit_proxy(phabricator_client)),
        ],
        lifespan=lifespan,
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
