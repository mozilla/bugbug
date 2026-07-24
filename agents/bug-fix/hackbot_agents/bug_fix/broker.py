"""Bugzilla MCP + Phabricator patch broker.

Sidecar container that holds the privileged API keys and serves them over HTTP
to the agent process (a sibling container in the same Cloud Run Job task), which
reaches us at `127.0.0.1:<port>`. The agent container itself binds no
credentials:

- Bugzilla: the `bugzilla` MCP tools over `/mcp` (read-only, live during the run).
- Phabricator: `GET /phabricator/revision/{id}/patch` returns a revision's base
  commit + raw diff, so the agent can check its source tree out at the revision
  before running (see ``revision.checkout_revision``).
"""

import logging
from contextlib import asynccontextmanager

import bugsy
import uvicorn
from agent_tools import bugzilla
from agent_tools.bugzilla import BugzillaContext
from agent_tools.claude_sdk import build_sdk_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from phabricator_client import PhabricatorClient, PhabricatorSettings
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

log = logging.getLogger("bugzilla-broker")


class BrokerInputs(BaseSettings):
    bugzilla_api_url: str
    bugzilla_api_key: str
    phabricator_url: str
    phabricator_api_key: str
    host: str = "0.0.0.0"
    port: int = 8765

    model_config = SettingsConfigDict(extra="ignore")


def _phabricator_route(settings: PhabricatorSettings) -> Route:
    """A read-only endpoint returning a revision's base commit + raw diff.

    The broker holds the Conduit key; the agent only ever sees this loopback URL,
    so it can reproduce the revision's tree without any credentials.
    """

    async def get_patch(request):
        revision_id = int(request.path_params["revision_id"])
        client = PhabricatorClient(settings)
        diff = await client.query_latest_diff(revision_id)
        if diff is None:
            return JSONResponse(
                {"error": f"D{revision_id} has no diffs"}, status_code=404
            )
        if not diff.base_commit:
            return JSONResponse(
                {"error": f"D{revision_id} diff {diff.id} has no base commit"},
                status_code=404,
            )
        raw_diff = await client.get_raw_diff(diff.id)
        # The recorded base is often an abbreviated hash; git can only fetch a
        # full object id, so expand it here (falling back to the raw value).
        base_commit = await client.resolve_commit(diff.base_commit) or diff.base_commit
        return JSONResponse({"base_commit": base_commit, "raw_diff": raw_diff})

    return Route("/phabricator/revision/{revision_id:int}/patch", get_patch)


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
                "broker ready on %s:%d (bugzilla read-only + phabricator patch)",
                inputs.host,
                inputs.port,
            )
            yield

    async def mcp_handler(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    phabricator_settings = PhabricatorSettings(
        url=inputs.phabricator_url, api_key=inputs.phabricator_api_key
    )
    return Starlette(
        routes=[
            Mount("/mcp", app=mcp_handler),
            _phabricator_route(phabricator_settings),
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
