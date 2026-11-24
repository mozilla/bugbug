# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""otari auth by GCP Workload Identity Federation, instead of an API key.

With ``OTARI_FEDERATION_RULE_ID`` set, the workload's Google identity token is
exchanged for short-lived otari access tokens. The harnesses read one static key
when they start, so they reach otari through a local proxy that puts a fresh
token on every request: ``OTARI_BASE_URL`` points at the proxy and
``OTARI_API_KEY`` is a placeholder for as long as it runs.

See https://docs.otari.ai/self-hosting/operations/workload-identity
"""

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import google.auth.transport.requests
import google.oauth2.id_token
import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

from . import otari

RULE_ID_ENV = "OTARI_FEDERATION_RULE_ID"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"
# A token is renewed this long before it expires.
RENEW_MARGIN_S = 60
# otari's default access token lifetime, for a response that does not say.
DEFAULT_TOKEN_TTL_S = 600
# Hop-by-hop, or set again for the upstream request.
DROPPED_REQUEST_HEADERS = {
    "host",
    "content-length",
    "connection",
    "authorization",
    "otari-key",
    "x-api-key",
}
DROPPED_RESPONSE_HEADERS = {"connection", "transfer-encoding", "keep-alive"}


def enabled() -> bool:
    return bool(os.environ.get(RULE_ID_ENV))


def _identity_token(audience: str) -> str:
    request = google.auth.transport.requests.Request()
    return google.oauth2.id_token.fetch_id_token(request, audience)


class AccessToken:
    """otari access tokens exchanged for the workload's identity, renewed as they expire."""

    def __init__(self, client: httpx.AsyncClient, base: str, rule_id: str):
        self._client, self._base, self._rule_id = client, base, rule_id
        parts = urlsplit(base)
        self._audience = f"{parts.scheme}://{parts.netloc}"
        self._token, self._expires = "", 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        async with self._lock:
            if time.monotonic() > self._expires - RENEW_MARGIN_S:
                await self._exchange()
            return self._token

    async def _exchange(self) -> None:
        identity = await asyncio.to_thread(_identity_token, self._audience)
        response = await self._client.post(
            f"{self._base}/v1/oauth/token",
            json={
                "grant_type": GRANT_TYPE,
                "assertion": identity,
                "federation_rule_id": self._rule_id,
            },
        )
        response.raise_for_status()
        grant = response.json()
        self._token = grant["access_token"]
        ttl = grant.get("expires_in") or DEFAULT_TOKEN_TTL_S
        self._expires = time.monotonic() + ttl


def proxy(client: httpx.AsyncClient, base: str, token: AccessToken) -> Starlette:
    """Forwards ``/api/...`` to ``base``, the real API root, as the workload."""

    async def forward(request: Request) -> StreamingResponse:
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in DROPPED_REQUEST_HEADERS
        }
        headers["Authorization"] = f"Bearer {await token.get()}"
        upstream = client.build_request(
            request.method,
            base + request.url.path.removeprefix("/api"),
            params=request.query_params,
            headers=headers,
            content=await request.body(),
        )
        response = await client.send(upstream, stream=True)
        return StreamingResponse(
            response.aiter_raw(),
            status_code=response.status_code,
            headers={
                k: v
                for k, v in response.headers.items()
                if k.lower() not in DROPPED_RESPONSE_HEADERS
            },
            background=BackgroundTask(response.aclose),
        )

    methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]
    return Starlette(routes=[Route("/api/{path:path}", forward, methods=methods)])


class _Server(uvicorn.Server):
    """Leaves the process's signal handling alone."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


@contextlib.asynccontextmanager
async def serving() -> AsyncIterator[None]:
    """Route otari through a federated proxy while in effect; inert without a rule."""
    if not enabled():
        yield
        return
    base = otari.api_url().removesuffix("/v1")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=None)) as client:
        token = AccessToken(client, base, os.environ[RULE_ID_ENV])
        # Fail here, not inside a harness, when the workload can't get a token.
        await token.get()
        server = _Server(
            uvicorn.Config(
                proxy(client, base, token),
                host="127.0.0.1",
                port=0,
                log_level="warning",
                lifespan="off",
            )
        )
        task = asyncio.create_task(server.serve())
        while not server.started:
            if task.done():
                task.result()
            await asyncio.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        saved = {k: os.environ.get(k) for k in ("OTARI_BASE_URL", otari.API_KEY_ENV)}
        os.environ["OTARI_BASE_URL"] = f"http://127.0.0.1:{port}/api"
        os.environ[otari.API_KEY_ENV] = "federated"
        try:
            yield
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            server.should_exit = True
            await task
