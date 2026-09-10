"""The HTTP surface: a default-deny proxy in front of Bugzilla's REST API.

The per-run token arrives in ``X-Bugzilla-API-Key``, where bugsy already puts an
API key, so an agent's client needs no special casing.

Two rules shape the code. **Default deny**: only four paths exist, and anything
else, or any method but GET, is a Bugzilla-shaped 101. **Authorize on upstream
truth**: a bug's own fields decide access, fetched with the proxy's credential,
so the caller's ``include_fields`` can narrow the response but not the decision.
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from typing import Any

from cachetools import TTLCache
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from bugzilla_proxy.config import Settings
from bugzilla_proxy.scope import AUTH_FIELDS, Grant, Scope
from bugzilla_proxy.tokens import TokenError, TokenVerifier
from bugzilla_proxy.upstream import Upstream, UpstreamError

log = logging.getLogger(__name__)

# 101 and 102 are what agent_tools.bugzilla already renders as
# `endpoint_not_exposed` and `access_denied`.
CODE_NOT_EXPOSED = 101
CODE_ACCESS_DENIED = 102
CODE_UPSTREAM = 103

# Credentials a caller might try to smuggle past our header swap.
_AUTH_PARAMS = frozenset(
    {
        "api_key",
        "Bugzilla_api_key",
        "Bugzilla_login",
        "Bugzilla_password",
        "Bugzilla_token",
        "login",
        "password",
        "token",
    }
)

# Group names `include_fields` accepts. They mean "widen", so they can never
# narrow a projection.
_FIELD_GROUPS = frozenset({"_default", "_all", "_extra", "_custom"})

_BUG_COMMENTS = re.compile(r"^bug/(\d+)/comment$")
_BUG_ATTACHMENTS = re.compile(r"^bug/(\d+)/attachment$")
_ATTACHMENT = re.compile(r"^bug/attachment/(\d+)$")

_ENDPOINT_BUG = "bug"
_ENDPOINT_COMMENTS = "bug/*/comment"
_ENDPOINT_ATTACHMENTS = "bug/*/attachment"
_ENDPOINT_ATTACHMENT = "bug/attachment/*"


def _error(code: int, message: str, status_code: int = 404) -> JSONResponse:
    """Render a Bugzilla-shaped error.

    bugsy turns any message mentioning an API key into a `LoginException`
    rather than the `BugsyException` the tools expect, so upstream complaints
    about credentials are replaced instead of relayed.
    """
    if "API key" in message:
        message = "Bugzilla rejected the proxy's credentials"
    return JSONResponse(
        {"error": True, "code": code, "message": message},
        status_code=status_code,
    )


def _requested_fields(params: dict[str, str]) -> frozenset[str] | None:
    """The caller's ``include_fields``, or None for no narrowing.

    A group name like ``_default`` asks to widen, which is the tier's decision
    rather than the caller's, so it counts as no narrowing at all.
    """
    raw = params.get("include_fields")
    if not raw:
        return None
    names = {name.strip() for name in str(raw).split(",") if name.strip()}
    if not names or names & _FIELD_GROUPS:
        return None
    return frozenset(names)


def _clean_params(request: Request) -> dict[str, Any]:
    """The caller's query params, minus anything that looks like a credential."""
    return {
        key: value
        for key, value in request.query_params.items()
        if key not in _AUTH_PARAMS
    }


def create_app(settings: Settings, upstream: Upstream | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app_: FastAPI):
        yield
        await app_.state.upstream.aclose()

    app = FastAPI(
        title="bugzilla-proxy", docs_url=None, redoc_url=None, lifespan=lifespan
    )

    app.state.settings = settings
    app.state.verifier = TokenVerifier(settings)
    app.state.upstream = upstream or Upstream(settings)
    # Keyed by bug id, not by token: this is upstream truth, identical for
    # every caller. The TTL bounds how long a newly-private bug keeps being
    # served.
    app.state.bug_cache = TTLCache(
        maxsize=settings.decision_cache_max_entries,
        ttl=settings.decision_cache_ttl_seconds,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    async def _auth_fields_for(app_: FastAPI, bug_id: int) -> dict[str, Any] | None:
        """Fetch just enough of a bug to decide access, memoised."""
        cache = app_.state.bug_cache
        if bug_id in cache:
            return cache[bug_id]
        payload = await app_.state.upstream.get(
            "bug",
            {"id": str(bug_id), "include_fields": ",".join(sorted(AUTH_FIELDS))},
        )
        bugs = payload.get("bugs") or []
        bug = bugs[0] if bugs else None
        cache[bug_id] = bug
        return bug

    def _audit(scope: Scope, event: str, **fields: Any) -> None:
        log.info(
            "bzproxy %s run=%s agent=%s requested_by=%s %s",
            event,
            scope.run_id,
            scope.agent,
            scope.requested_by or "-",
            " ".join(f"{k}={v}" for k, v in fields.items()),
        )

    def _authenticate(request: Request) -> Scope:
        token = request.headers.get("X-Bugzilla-API-Key", "")
        return request.app.state.verifier.verify(token)

    async def _search(request: Request, scope: Scope) -> JSONResponse:
        if not any(g.allows_endpoint(_ENDPOINT_BUG) for g in scope.grants):
            return _error(CODE_NOT_EXPOSED, "This proxy does not expose bug search.")

        params = _clean_params(request)
        requested = _requested_fields(params)

        upstream_fields = scope.upstream_fields(requested)
        if upstream_fields:
            params["include_fields"] = ",".join(sorted(upstream_fields))
        else:
            params.pop("include_fields", None)

        limit = params.get("limit")
        try:
            capped = min(int(limit), settings.max_search_limit) if limit else None
        except (TypeError, ValueError):
            return _error(CODE_UPSTREAM, "The 'limit' parameter must be a number.", 400)
        params["limit"] = str(capped or settings.max_search_limit)

        payload = await request.app.state.upstream.get("bug", params)

        visible: list[dict[str, Any]] = []
        for bug in payload.get("bugs") or []:
            grant = scope.grant_for_endpoint(bug, _ENDPOINT_BUG)
            if grant is not None:
                visible.append(grant.project(bug, requested))

        returned = len(payload.get("bugs") or [])
        _audit(
            scope,
            "search",
            upstream=returned,
            visible=len(visible),
            ids=",".join(str(b.get("id")) for b in visible) or "-",
        )
        return JSONResponse({"bugs": visible})

    async def _authorized_grant(
        request: Request, scope: Scope, bug_id: int, endpoint: str
    ) -> tuple[Grant | None, JSONResponse | None]:
        """The grant covering ``endpoint`` on ``bug_id``, or a denial.

        Out-of-scope and nonexistent give the same answer on purpose:
        distinguishing them confirms the existence of bugs the run may not see.
        """
        bug = await _auth_fields_for(request.app, bug_id)
        if bug is None:
            _audit(scope, "deny", bug=bug_id, endpoint=endpoint, reason="not_found")
            return None, _error(
                CODE_ACCESS_DENIED, f"Bug {bug_id} is not available to this run."
            )
        grant = scope.grant_for_endpoint(bug, endpoint)
        if grant is None:
            _audit(scope, "deny", bug=bug_id, endpoint=endpoint, reason="out_of_scope")
            return None, _error(
                CODE_ACCESS_DENIED, f"Bug {bug_id} is not available to this run."
            )
        _audit(scope, "allow", bug=bug_id, endpoint=endpoint, tier=grant.tier)
        return grant, None

    async def _comments(request: Request, scope: Scope, bug_id: int) -> JSONResponse:
        _grant, denial = await _authorized_grant(
            request, scope, bug_id, _ENDPOINT_COMMENTS
        )
        if denial is not None:
            return denial
        payload = await request.app.state.upstream.get(
            f"bug/{bug_id}/comment", _clean_params(request)
        )
        return JSONResponse(payload)

    async def _attachments(request: Request, scope: Scope, bug_id: int) -> JSONResponse:
        if not scope.attachments:
            return _error(
                CODE_NOT_EXPOSED, "This run may not read Bugzilla attachments."
            )
        _grant, denial = await _authorized_grant(
            request, scope, bug_id, _ENDPOINT_ATTACHMENTS
        )
        if denial is not None:
            return denial
        payload = await request.app.state.upstream.get(
            f"bug/{bug_id}/attachment", _clean_params(request)
        )
        return JSONResponse(payload)

    async def _attachment(
        request: Request, scope: Scope, attachment_id: int
    ) -> JSONResponse:
        """One attachment by its own id.

        Its bug is only discoverable from the attachment, so this fetches first
        and authorizes second. Nothing is returned until the bug clears.
        """
        if not scope.attachments:
            return _error(
                CODE_NOT_EXPOSED, "This run may not read Bugzilla attachments."
            )
        payload = await request.app.state.upstream.get(
            f"bug/attachment/{attachment_id}", _clean_params(request)
        )
        attachments = payload.get("attachments") or {}
        record = attachments.get(str(attachment_id)) or attachments.get(attachment_id)
        bug_id = (record or {}).get("bug_id")
        if bug_id is None:
            return _error(
                CODE_ACCESS_DENIED,
                f"Attachment {attachment_id} is not available to this run.",
            )
        _grant, denial = await _authorized_grant(
            request, scope, int(bug_id), _ENDPOINT_ATTACHMENT
        )
        if denial is not None:
            return denial
        return JSONResponse(payload)

    @app.get("/rest/{path:path}")
    async def rest(path: str, request: Request) -> JSONResponse:
        try:
            scope = _authenticate(request)
        except TokenError as exc:
            log.warning("Rejected request to %s: %s", path, exc)
            return _error(CODE_ACCESS_DENIED, "This run is not authorized.", 401)

        route = path.strip("/")
        try:
            if route == "bug":
                return await _search(request, scope)
            match = _BUG_COMMENTS.match(route)
            if match:
                return await _comments(request, scope, int(match.group(1)))
            match = _BUG_ATTACHMENTS.match(route)
            if match:
                return await _attachments(request, scope, int(match.group(1)))
            match = _ATTACHMENT.match(route)
            if match:
                return await _attachment(request, scope, int(match.group(1)))
        except UpstreamError as exc:
            log.warning("Upstream failure serving %s: %s", route, exc)
            return _error(CODE_UPSTREAM, str(exc), 502)

        _audit(scope, "deny", endpoint=route, reason="not_exposed")
        return _error(CODE_NOT_EXPOSED, f"This proxy does not expose '/{route}'.")

    @app.api_route(
        "/rest/{path:path}",
        methods=["POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def rest_write(path: str) -> JSONResponse:
        """Writes never reach upstream.

        Agents change the world through recorded actions, applied by
        hackbot-api after a run is known good.
        """
        return _error(CODE_NOT_EXPOSED, "This proxy is read-only.", status_code=405)

    return app
