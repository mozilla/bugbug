"""The `/rest` router: a default-deny proxy in front of Bugzilla's REST API.

The per-run token arrives in ``X-Bugzilla-API-Key``, the header Bugzilla clients
already send a key in, so a caller needs no special casing to reach us.

Two rules shape the code. **Default deny**: only five paths exist, and anything
else, or any method but GET, is a Bugzilla-shaped 101. **Authorize on upstream
truth**: a bug's own fields decide access, fetched with the proxy's credential,
so the caller's ``include_fields`` can narrow the response but not the decision.

What a request may ask for, and how responses are cut down, is in
:mod:`bugzilla_proxy.params`; which key reads, in :mod:`bugzilla_proxy.upstream`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from bugzilla_proxy.params import (
    ATTACHMENT_PARAMS,
    COMMENT_PARAMS,
    INLINE_EXTRAS,
    RequestRejected,
    add_public_only_condition,
    all_params,
    allowed_params,
    as_requested,
    comment_bug_ids,
    field_names,
    only_keys,
    reject_credentials,
    requested_fields,
    requiring_is_private,
    search_limit,
    visible_records,
)
from bugzilla_proxy.scope import AUTH_FIELDS, Grant, Scope
from bugzilla_proxy.tokens import KeySourceUnavailable, TokenError
from bugzilla_proxy.upstream import PRIVILEGED, Backend, UpstreamError, key_for

log = logging.getLogger(__name__)

router = APIRouter()

# Errors: Bugzilla-shaped, so the agents' client reads them unchanged.

# 101 and 102 are what agent_tools.bugzilla already renders as
# `endpoint_not_exposed` and `access_denied`.
CODE_NOT_EXPOSED = 101
CODE_ACCESS_DENIED = 102
CODE_UPSTREAM = 103
# Phrases marking an upstream complaint about *our* credential. The exact list
# also matters to clients, which read it to tell an auth failure from an
# ordinary one; see tests/test_error_contract.py before changing it.
CREDENTIAL_PHRASES = ("API key", "username or password")


def error(code: int, message: str, status_code: int = 404) -> JSONResponse:
    """Render a Bugzilla-shaped error.

    An upstream complaint about credentials is replaced rather than relayed: it
    is about the proxy's own credential, not the caller's token, so passing it
    on would both mislead the caller and report our credential state to them.
    """
    if any(phrase in message for phrase in CREDENTIAL_PHRASES):
        message = "Bugzilla rejected the proxy's credentials"
    return JSONResponse(
        {"error": True, "code": code, "message": message},
        status_code=status_code,
    )


# Every reason a bug or attachment is unavailable gets one of these, naming
# only what the caller asked for: the proxy's credential may see more than the
# caller could, so the reason goes to the audit log, never to the caller.


def bug_denied(bug_id: int) -> JSONResponse:
    return error(
        CODE_ACCESS_DENIED,
        f"Bug {bug_id} does not exist or is outside this run's Bugzilla scope.",
    )


def bugs_denied(bug_ids: list[int]) -> JSONResponse:
    listed = ", ".join(str(i) for i in bug_ids)
    return error(
        CODE_ACCESS_DENIED,
        f"Bug(s) {listed} in 'ids' do not exist or are outside this "
        "run's Bugzilla scope. Remove them and retry.",
    )


def attachment_denied(attachment_id: int) -> JSONResponse:
    return error(
        CODE_ACCESS_DENIED,
        f"Attachment {attachment_id} does not exist or is outside this run's "
        "Bugzilla scope.",
    )


# Endpoints.

# The endpoint patterns a grant names, as `tokens.KNOWN_ENDPOINTS` lists them.
ENDPOINT_BUG = "bug"
ENDPOINT_COMMENTS = "bug/*/comment"
ENDPOINT_ATTACHMENTS = "bug/*/attachment"
ENDPOINT_ATTACHMENT = "bug/attachment/*"


def audit(scope: Scope, event: str, **fields: Any) -> None:
    # The `audit` prefix is what separates the access trail from ordinary
    # logging; the service name is already on every line as the logger name.
    log.info(
        "audit %s run=%s agent=%s requested_by=%s %s",
        event,
        scope.run_id,
        scope.agent,
        scope.requested_by or "-",
        " ".join(f"{k}={v}" for k, v in fields.items()),
    )


def _backend(request: Request) -> Backend:
    return request.state.backend


def _decide(
    scope: Scope, bug_id: int, bug: dict[str, Any] | None, endpoint: str
) -> Grant | None:
    """The grant covering ``endpoint`` on ``bug``, or None for a denial.

    Every denial gets the same answer, whatever the reason: nonexistent, in a
    group the token lacks, outside the anchor, or at a tier without this
    endpoint. The proxy's credential may see more than the caller could, so
    naming the reason would report what that credential saw. The reason goes
    to the audit log only.
    """
    if bug is None:
        audit(scope, "deny", bug=bug_id, endpoint=endpoint, reason="not_found")
        return None
    grant = scope.grant_for_endpoint(bug, endpoint)
    if grant is None:
        audit(scope, "deny", bug=bug_id, endpoint=endpoint, reason="out_of_scope")
        return None
    audit(scope, "allow", bug=bug_id, endpoint=endpoint, tier=grant.tier)
    return grant


async def _grant_for(
    request: Request, scope: Scope, bug_id: int, endpoint: str
) -> Grant | None:
    bugs = await _backend(request).auth_fields([bug_id])
    return _decide(scope, bug_id, bugs[bug_id], endpoint)


async def search(request: Request, scope: Scope) -> JSONResponse:
    if not any(g.allows_endpoint(ENDPOINT_BUG) for g in scope.grants):
        return error(CODE_NOT_EXPOSED, "This proxy does not expose bug search.")

    params = all_params(request)
    requested = requested_fields(params, scope.exposed_fields(ENDPOINT_BUG))

    upstream_fields = scope.upstream_fields(requested)
    if upstream_fields:
        params["include_fields"] = ",".join(sorted(upstream_fields))
    else:
        params.pop("include_fields", None)

    # The caller may exclude fields the decision needs. Those are still
    # fetched, and only taken off the response, so excluding `groups` hides
    # the field rather than quietly denying every bug.
    excluded = field_names(params.pop("exclude_fields", None))
    if excluded - AUTH_FIELDS:
        params["exclude_fields"] = ",".join(sorted(excluded - AUTH_FIELDS))

    settings = request.app.state.settings
    params["limit"] = str(search_limit(params.get("limit"), settings.max_search_limit))
    if not scope.is_private:
        add_public_only_condition(params)

    payload = await _backend(request).upstream.get("bug", params)

    visible: list[dict[str, Any]] = []
    for bug in payload.get("bugs") or []:
        grant = scope.grant_for_endpoint(bug, ENDPOINT_BUG)
        if grant is not None:
            served = grant.project(bug, requested)
            # Refused up front; dropped again in case upstream sends them.
            for name in (*INLINE_EXTRAS, *excluded):
                served.pop(name, None)
            visible.append(served)

    audit(
        scope,
        "search",
        upstream=len(payload.get("bugs") or []),
        visible=len(visible),
        ids=",".join(str(b.get("id")) for b in visible) or "-",
    )
    return JSONResponse({"bugs": visible})


async def comments(request: Request, scope: Scope, bug_id: int) -> JSONResponse:
    """Comments for the bug in the path, plus any in ``ids``.

    Each bug is authorized on its own, and one the run may not see fails the
    whole request, naming it. That matches BMO, which fails the call on an
    inaccessible id rather than leaving it out, so the caller learns nothing
    it would not learn from BMO directly.
    """
    params = allowed_params(request, COMMENT_PARAMS, f"bug/{bug_id}/comment")
    params.pop("ids", None)
    settings = request.app.state.settings
    wanted = comment_bug_ids(request, bug_id, settings.max_search_limit)
    bugs = await _backend(request).auth_fields(wanted)

    first = _decide(scope, bug_id, bugs[bug_id], ENDPOINT_COMMENTS)
    if first is None:
        return bug_denied(bug_id)
    grants = {str(bug_id): first}
    denied: list[int] = []
    for other in wanted[1:]:
        grant = _decide(scope, other, bugs[other], ENDPOINT_COMMENTS)
        if grant is None:
            denied.append(other)
        else:
            grants[str(other)] = grant
    if denied:
        return bugs_denied(denied)

    upstream_params = requiring_is_private(params)
    others = [key for key in grants if key != str(bug_id)]
    if others:
        upstream_params["ids"] = ",".join(others)
    payload = await _backend(request).upstream.get(
        f"bug/{bug_id}/comment", upstream_params
    )
    # Only the authorized bugs, and never the `comments` map, which is keyed by
    # comment id and is how `comment_ids` results come back.
    served: dict[str, Any] = {}
    for key, entry in only_keys(payload.get("bugs"), set(grants)).items():
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry["comments"] = as_requested(
            visible_records(entry.get("comments"), grants[key]), params
        )
        served[key] = entry
    return JSONResponse({"bugs": served})


def _attachments_off() -> JSONResponse:
    return error(CODE_NOT_EXPOSED, "This run may not read Bugzilla attachments.")


async def bug_attachments(request: Request, scope: Scope, bug_id: int) -> JSONResponse:
    if not scope.attachments:
        return _attachments_off()
    params = allowed_params(request, ATTACHMENT_PARAMS, f"bug/{bug_id}/attachment")
    grant = await _grant_for(request, scope, bug_id, ENDPOINT_ATTACHMENTS)
    if grant is None:
        return bug_denied(bug_id)
    payload = await _backend(request).upstream.get(
        f"bug/{bug_id}/attachment", requiring_is_private(params)
    )
    # Only the authorized bug, and never the `attachments` map, which is keyed
    # by attachment id and is how `attachment_ids` results come back.
    records = only_keys(payload.get("bugs"), {str(bug_id)}).get(str(bug_id))
    served = as_requested(visible_records(records, grant), params)
    return JSONResponse({"bugs": {str(bug_id): served}})


async def attachment(request: Request, scope: Scope, attachment_id: int) -> Response:
    """One attachment by its own id.

    Its bug is only discoverable from the attachment, so this first reads just
    enough of it to decide, then streams the full response as BMO sends it.
    Attachment data can be large and is never held in memory. Nothing is
    requested beyond the metadata until the bug clears.
    """
    if not scope.attachments:
        return _attachments_off()
    path = f"bug/attachment/{attachment_id}"
    params = allowed_params(request, ATTACHMENT_PARAMS, path)
    upstream = _backend(request).upstream

    def denied(reason: str) -> JSONResponse:
        audit(
            scope,
            "deny",
            attachment=attachment_id,
            endpoint=ENDPOINT_ATTACHMENT,
            reason=reason,
        )
        return attachment_denied(attachment_id)

    try:
        meta = await upstream.get(path, {"include_fields": "id,bug_id,is_private"})
    except UpstreamError as exc:
        if exc.status_code >= 500:
            raise
        # BMO refusing the proxy's own credential is one more reason the
        # attachment is unavailable, and gets the same answer as the rest.
        return denied("upstream_refused")
    record = only_keys(meta.get("attachments"), {str(attachment_id)}).get(
        str(attachment_id)
    )
    bug_id = (record or {}).get("bug_id")
    if bug_id is None:
        return denied("not_found")
    grant = await _grant_for(request, scope, int(bug_id), ENDPOINT_ATTACHMENT)
    if grant is None:
        # Answered as the attachment, not its bug: naming the bug would reveal
        # which bug a hidden attachment belongs to.
        return attachment_denied(attachment_id)
    if not visible_records([record], grant):
        return denied("private_attachment")

    # Cleared. Relayed unparsed, so what keeps the body to this one attachment
    # is the parameter allowlist: `ids` and `attachment_ids`, which would add
    # others, are refused above.
    response = await upstream.open_stream(path, params)
    if response.status_code >= 400:
        await response.aclose()
        if response.status_code >= 500:
            raise UpstreamError("Bugzilla returned a server error")
        return denied("upstream_refused")
    return StreamingResponse(
        response.aiter_bytes(),
        media_type=response.headers.get("content-type", "application/json"),
        background=BackgroundTask(response.aclose),
    )


# Routing.

_BUG_COMMENTS = re.compile(r"^bug/(\d+)/comment$")
_BUG_ATTACHMENTS = re.compile(r"^bug/(\d+)/attachment$")
_ATTACHMENT = re.compile(r"^bug/attachment/(\d+)$")


async def _authenticate(request: Request) -> Scope:
    token = request.headers.get("X-Bugzilla-API-Key", "")
    # In a thread: fetching the issuer's keys is a blocking HTTP call.
    return await asyncio.to_thread(request.app.state.verifier.verify, token)


async def _route(request: Request, scope: Scope, route: str) -> Response | None:
    """Serve ``route``, or None if it is not one of the exposed paths."""
    if route == "version":
        # Brokers probe this at startup to check URL and key. It is not bug
        # data, so any valid token may read it, and only the version string is
        # passed on.
        payload = await request.state.backend.upstream.get("version", {})
        return JSONResponse({"version": payload.get("version")})
    if route == "bug":
        return await search(request, scope)
    if match := _BUG_COMMENTS.match(route):
        return await comments(request, scope, int(match.group(1)))
    if match := _BUG_ATTACHMENTS.match(route):
        return await bug_attachments(request, scope, int(match.group(1)))
    if match := _ATTACHMENT.match(route):
        return await attachment(request, scope, int(match.group(1)))
    return None


@router.get("/rest/{path:path}")
async def rest(path: str, request: Request) -> Response:
    try:
        scope = await _authenticate(request)
    except KeySourceUnavailable as exc:
        log.warning("Could not verify a request to %s: %s", path, exc)
        return error(CODE_UPSTREAM, "Token verification is unavailable.", 503)
    except TokenError as exc:
        log.warning("Rejected request to %s: %s", path, exc)
        return error(CODE_ACCESS_DENIED, f"This run is not authorized: {exc}.", 401)

    route = path.strip("/")
    key_name = key_for(scope)
    backend = request.app.state.backends.get(key_name)
    if backend is None:
        log.error("No %s Bugzilla API key configured", key_name)
        reach = "with" if key_name == PRIVILEGED else "without"
        return error(
            CODE_UPSTREAM,
            f"This proxy has no {key_name} Bugzilla key configured, so it "
            f"cannot serve tokens {reach} private grants.",
            503,
        )
    request.state.backend = backend

    try:
        reject_credentials(request)
        response = await _route(request, scope, route)
    except RequestRejected as exc:
        audit(scope, "reject", endpoint=route, code=exc.code)
        return error(exc.code, str(exc), exc.status_code)
    except UpstreamError as exc:
        log.warning("Upstream failure serving %s: %s", route, exc)
        return error(CODE_UPSTREAM, str(exc), 502)

    if response is None:
        audit(scope, "deny", endpoint=route, reason="not_exposed")
        return error(CODE_NOT_EXPOSED, f"This proxy does not expose '/{route}'.")
    return response


@router.api_route(
    "/rest/{path:path}",
    methods=["POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def rest_write(path: str) -> JSONResponse:
    """Writes never reach upstream.

    Agents change the world through recorded actions, applied by hackbot-api
    after a run is known good.
    """
    return error(CODE_NOT_EXPOSED, "This proxy is read-only.", status_code=405)
