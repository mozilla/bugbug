"""Bugzilla MCP + read-only Phabricator broker.

Sidecar container that holds the privileged API keys and serves them over HTTP
to the agent process (a sibling container in the same Cloud Run Job task), which
reaches us at `127.0.0.1:<port>`. The agent container itself binds no
credentials:

- Bugzilla: the `bugzilla` MCP tools over `/bugzilla/mcp` (read-only, live
  during the run).
- Phabricator: the read-only `phabricator` MCP tools over `/phabricator/mcp`, so
  a follow-up run can read the revision it was called on: its metadata, the
  full comment thread, and where each inline comment sits.
- Phabricator: `/phabricator/api` mounts a read-only Conduit proxy
  (``phabricator_proxy``) — the broker looks enough like a Phabricator instance
  for a Conduit client to talk to it, but only for an allow-listed set of read
  methods, and it swaps in the real key so the caller never holds one.
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

log = logging.getLogger("bugzilla-broker")


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
            Mount("/bugzilla/mcp", app=_mcp_endpoint(bugzilla_manager)),
            Mount("/phabricator/mcp", app=_mcp_endpoint(phabricator_manager)),
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
    app = build_app(inputs)
    uvicorn.run(app, host=inputs.host, port=inputs.port, log_config=None)


if __name__ == "__main__":
    main()
