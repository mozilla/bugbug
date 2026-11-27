"""Builds the app. The endpoints live in :mod:`bugzilla_proxy.routers.rest`."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from bugzilla_proxy.config import Settings
from bugzilla_proxy.routers import rest
from bugzilla_proxy.tokens import TokenVerifier
from bugzilla_proxy.upstream import Upstream, build_backends


def create_app(
    settings: Settings,
    privileged_upstream: Upstream | None = None,
    unprivileged_upstream: Upstream | None = None,
) -> FastAPI:
    """Build the app.

    A factory rather than a module-level ``app``, unlike the other services
    here, so that ``settings`` is an argument: verification refuses to start
    without a key source, which would otherwise make importing this module
    depend on the environment being set up first. ``main`` still exposes a
    module-level ``app``, so deployment sees the usual shape.
    """
    backends = build_backends(settings, privileged_upstream, unprivileged_upstream)

    @asynccontextmanager
    async def lifespan(app_: FastAPI):
        yield
        for backend in app_.state.backends.values():
            if backend is not None:
                await backend.upstream.aclose()

    app = FastAPI(
        title="bugzilla-proxy", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.verifier = TokenVerifier(settings)
    app.state.backends = backends

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint for Cloud Run."""
        return {"message": "Service is healthy", "status": "ok"}

    app.include_router(rest.router)
    return app
