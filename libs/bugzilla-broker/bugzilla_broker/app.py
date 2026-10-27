"""Bugzilla MCP broker.

The broker is a sidecar container. It holds the Bugzilla API key and serves the
`bugzilla` MCP tools over HTTP. The agent runs in another container of the same Cloud
Run job, and it connects to `127.0.0.1:<port>/mcp`. The agent container has no Bugzilla
key.

It has two modes:

- By default, it serves the agent-tools Bugzilla tools as they are. They return all
  that the API key can read, also the restricted bugs. This mode is for an agent whose
  output is not public.
- With `public_only`, it serves only what a logged-out reader sees: see
  `bugzilla_broker.public`. This mode is for an agent that posts public comments.

Each tool runs off the event loop and has a request timeout: see
`bugzilla_broker.blocking`.
"""

import dataclasses
import logging
from contextlib import asynccontextmanager

import bugsy
import uvicorn
from agent_tools import bugzilla
from agent_tools.bugzilla import BugzillaContext
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.registry import ToolDefinition
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.applications import Starlette
from starlette.routing import Mount

from bugzilla_broker.blocking import bound_session, off_loop
from bugzilla_broker.public import DEFAULT_ATTACHMENT_HINT, public_tools

log = logging.getLogger("bugzilla-broker")


class BrokerSettings(BaseSettings):
    """The broker settings, read from the environment.

    Each setting comes from the variable of the same name in upper case, for example
    `PORT` or `PUBLIC_ONLY`.
    """

    bugzilla_api_url: str
    bugzilla_api_key: str
    host: str = "0.0.0.0"
    # The default port of the Hackbot agent brokers. Set `PORT` to use another one, and
    # give the agent the same port in its `BROKER_URL`.
    port: int = 8765
    # Serve only what a logged-out reader sees.
    public_only: bool = False
    # How the agent gets an attachment that is not inlined, with `public_only`.
    attachment_hint: str = DEFAULT_ATTACHMENT_HINT
    # Allow `longdesc`, a search of comment text, with `public_only`.
    comment_search: bool = True

    model_config = SettingsConfigDict(extra="ignore")

    @model_validator(mode="after")
    def _public_settings_need_public_only(self):
        if not self.public_only and (
            not self.comment_search or self.attachment_hint != DEFAULT_ATTACHMENT_HINT
        ):
            raise ValueError("attachment_hint and comment_search need public_only")
        return self


def served_tools(settings: BrokerSettings) -> list[ToolDefinition]:
    """The tools that the broker serves. Each tool runs off the event loop."""
    tools = (
        public_tools(settings.attachment_hint, settings.comment_search)
        if settings.public_only
        else bugzilla.TOOLS
    )
    return [dataclasses.replace(t, handler=off_loop(t.handler)) for t in tools]


def mcp_manager(settings: BrokerSettings) -> StreamableHTTPSessionManager:
    """The MCP session manager of the `bugzilla` tools, for an app to mount and run."""
    client = bugsy.Bugsy(
        api_key=settings.bugzilla_api_key, bugzilla_url=settings.bugzilla_api_url
    )
    bound_session(client)
    ctx = BugzillaContext(client=client)
    sdk_config = build_sdk_server("bugzilla", ctx, served_tools(settings))
    return StreamableHTTPSessionManager(app=sdk_config["instance"], stateless=True)


def create_app(settings: BrokerSettings) -> Starlette:
    manager = mcp_manager(settings)

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            log.info(
                "bugzilla broker ready on %s:%d (read-only%s)",
                settings.host,
                settings.port,
                ", public only" if settings.public_only else "",
            )
            yield

    return Starlette(
        routes=[Mount("/mcp", app=manager.handle_request)], lifespan=lifespan
    )


def main(settings: BrokerSettings | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    settings = settings or BrokerSettings()
    uvicorn.run(
        create_app(settings), host=settings.host, port=settings.port, log_config=None
    )
