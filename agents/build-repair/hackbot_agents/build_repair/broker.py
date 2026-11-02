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
from pydantic import field_validator
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

    @field_validator("bugzilla_api_url")
    @classmethod
    def _ensure_rest_base(cls, v: str) -> str:
        """Bugsy expects the REST base (``.../rest``) and just appends the path.

        A bare host like ``https://bugzilla.mozilla.org`` makes every call hit
        the HTML site and fail to parse as JSON, so normalize it here.
        """
        v = v.rstrip("/")
        return v if v.endswith("/rest") else f"{v}/rest"


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
        # Probe Bugzilla once at startup so a bad API URL/key surfaces here as a
        # clear log line instead of an opaque JSON-decode error on every tool
        # call. We stay up regardless: the agent then gets a structured error.
        try:
            version = client.request("version").get("version")
            log.info(
                "bugzilla reachable at %s (version %s)",
                inputs.bugzilla_api_url,
                version,
            )
        except Exception:
            log.exception(
                "bugzilla health check failed against %s -- check BUGZILLA_API_URL "
                "and BUGZILLA_API_KEY; tool calls will fail until this is fixed",
                inputs.bugzilla_api_url,
            )
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
