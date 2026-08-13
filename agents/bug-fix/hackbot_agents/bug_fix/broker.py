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
- Phabricator: `POST /api/{method}` is a read-only Conduit façade — the broker
  looks enough like a Phabricator instance for a Conduit client to talk to it,
  but only for an allow-listed set of read methods, and it swaps in the real key
  so the caller never holds one. That is how the agent checks its source tree
  out at a (possibly stacked) revision before running, driving moz-phab's own
  Conduit client against this URL (see ``revision.checkout_revision``).
"""

import json
import logging
import urllib.parse
from contextlib import asynccontextmanager

import bugsy
import httpx
import uvicorn
from agent_tools import bugzilla
from agent_tools import phabricator as phabricator_tools
from agent_tools.bugzilla import BugzillaContext
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.phabricator import PhabricatorContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from phabricator_client import PhabricatorClient, PhabricatorSettings
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

log = logging.getLogger("bugzilla-broker")

# The only Conduit methods the proxy will forward. Everything needed to
# reconstruct a revision's stack and its diffs, and nothing that writes:
# an allow list (not a deny list) so a Conduit method added upstream is
# refused by default rather than silently exposed with the broker's key.
READ_ONLY_CONDUIT_METHODS = frozenset(
    {
        # The stack graph and each revision's current diff PHID.
        "differential.revision.search",
        # Diff metadata, including the base commit each diff was built on.
        "differential.diff.search",
        # The patch text itself.
        "differential.getrawdiff",
        # Expanding an abbreviated base commit to a full, fetchable hash.
        "diffusion.querycommits",
    }
)


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


def _conduit_error(code: str, info: str, status: int) -> JSONResponse:
    """A Conduit-shaped error body, so a Conduit client can read the refusal."""
    return JSONResponse(
        {"result": None, "error_code": code, "error_info": info}, status_code=status
    )


def _conduit_proxy_endpoint(client: PhabricatorClient):
    """A read-only Conduit proxy: `POST /api/{method}`.

    The broker holds the Conduit key; the agent only ever sees this loopback
    URL, so it can read what it needs to reconstruct a revision's stack without
    any credentials. Requests are forwarded only for
    :data:`READ_ONLY_CONDUIT_METHODS`, and whatever ``__conduit__`` token the
    caller sent is discarded and replaced with the real key by
    ``PhabricatorClient.conduit_call``.

    The body is parsed as a url-encoded form directly rather than via
    ``request.form()``: moz-phab's Conduit client posts a url-encoded body with
    no ``Content-Type`` header, which ``request.form()`` would read as empty.
    """

    async def proxy(request):
        method = request.path_params["method"]
        if method not in READ_ONLY_CONDUIT_METHODS:
            log.warning("refusing to proxy Conduit method %s", method)
            return _conduit_error(
                "ERR-CONDUIT-METHOD-NOT-ALLOWED",
                f"{method} is not on the broker's read-only allow list",
                403,
            )

        body = urllib.parse.parse_qs((await request.body()).decode())
        try:
            params = json.loads(body.get("params", ["{}"])[0])
        except ValueError as exc:
            return _conduit_error("ERR-CONDUIT-CORE", f"Malformed params: {exc}", 400)
        if not isinstance(params, dict):
            return _conduit_error(
                "ERR-CONDUIT-CORE", "params must be a JSON object", 400
            )

        try:
            return JSONResponse(await client.conduit_call(method, params))
        except httpx.HTTPError as exc:
            log.warning("proxied Conduit call %s failed: %s", method, exc)
            return _conduit_error(
                "ERR-CONDUIT-CORE", f"Upstream Phabricator call failed: {exc}", 502
            )

    return proxy


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
            # Mounted at the root, mirroring a real Phabricator instance's
            # layout: a Conduit client derives its API URL as `<base>/api/`,
            # so this is the path that makes the broker usable as one.
            Route(
                "/api/{method}",
                _conduit_proxy_endpoint(phabricator_client),
                methods=["POST"],
            ),
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
