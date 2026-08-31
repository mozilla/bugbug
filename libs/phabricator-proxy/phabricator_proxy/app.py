"""A read-only Conduit proxy: enough of Phabricator to be talked to, no key handed out.

:func:`create_app` returns an app to mount wherever a caller can reach it (for
hackbot, on the broker sidecar the agent talks to over loopback). Point a Conduit
client at the mount point and it behaves like a Phabricator instance, except that
the proxy holds the API key and the caller does not. Two rules keep that safe:

* only allow-listed methods are forwarded, so the key cannot be used to write;
  and
* whatever ``__conduit__`` token the caller sends is discarded and replaced with
  the real key, so a caller chooses the method and its arguments, never the
  credentials.

Requests and responses are otherwise relayed as-is — including Conduit's own
error envelope — so a client's existing error handling keeps working.
"""

from __future__ import annotations

import json
import logging
import urllib.parse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from phabricator_client import PhabricatorClient

log = logging.getLogger(__name__)

# Everything needed to reconstruct a revision's stack and its diffs, and nothing
# that writes. An allow list rather than a deny list, so a Conduit method added
# upstream is refused by default instead of silently exposed with the key.
READ_ONLY_METHODS = frozenset(
    {
        # A revision's metadata and the stack graph it sits in.
        "differential.revision.search",
        # A revision's diffs, and the base commit each was built on.
        "differential.querydiffs",
        # The patch text itself.
        "differential.getrawdiff",
        # Expanding an abbreviated base commit to a full, fetchable hash.
        "diffusion.querycommits",
    }
)


def _conduit_error(code: str, info: str, status: int) -> JSONResponse:
    """A Conduit-shaped error body, so a Conduit client can read the refusal."""
    return JSONResponse(
        {"result": None, "error_code": code, "error_info": info}, status_code=status
    )


def _parse_params(body: bytes) -> dict:
    """Read Conduit's ``params`` field out of a url-encoded request body.

    Parsed directly rather than via ``request.form()``: moz-phab's Conduit client
    posts a url-encoded body with no ``Content-Type`` header, which
    ``request.form()`` would read as empty.

    Raises :class:`ValueError` if ``params`` is not a JSON object.
    """
    fields = urllib.parse.parse_qs(body.decode())
    params = json.loads(fields.get("params", ["{}"])[0])
    if not isinstance(params, dict):
        raise ValueError("params must be a JSON object")
    return params


def create_app(
    client: PhabricatorClient,
    *,
    allowed_methods: frozenset[str] = READ_ONLY_METHODS,
) -> FastAPI:
    """Build the proxy app, forwarding through ``client`` (which holds the key).

    Mount it at the path a Conduit client will treat as the API root, e.g.
    ``Mount("/phabricator/api", app=create_app(client))`` makes a call to
    ``differential.revision.search`` land on
    ``/phabricator/api/differential.revision.search``.
    """
    # No docs/OpenAPI routes: this app fronts a credential, so it serves the
    # Conduit methods it was asked to serve and nothing else.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/{method}")
    async def proxy(method: str, request: Request) -> JSONResponse:
        if method not in allowed_methods:
            log.warning("refusing to proxy Conduit method %s", method)
            return _conduit_error(
                "ERR-CONDUIT-METHOD-NOT-ALLOWED",
                f"{method} is not on the proxy's read-only allow list",
                403,
            )

        try:
            params = _parse_params(await request.body())
        except ValueError as exc:
            return _conduit_error("ERR-CONDUIT-CORE", f"Malformed params: {exc}", 400)

        try:
            return JSONResponse(await client.conduit_call(method, params))
        except httpx.HTTPError as exc:
            log.warning("proxied Conduit call %s failed: %s", method, exc)
            return _conduit_error(
                "ERR-CONDUIT-CORE", f"Upstream Phabricator call failed: {exc}", 502
            )

    return app
