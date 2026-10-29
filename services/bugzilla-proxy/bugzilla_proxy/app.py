"""The HTTP surface: a default-deny proxy in front of Bugzilla's REST API.

The per-run token arrives in ``X-Bugzilla-API-Key``, the header Bugzilla clients
already send a key in, so a caller needs no special casing to reach us.

Two rules shape the code. **Default deny**: only five paths exist, and anything
else, or any method but GET, is a Bugzilla-shaped 101. **Authorize on upstream
truth**: a bug's own fields decide access, fetched with the proxy's credential,
so the caller's ``include_fields`` can narrow the response but not the decision.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from cachetools import TTLCache
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from bugzilla_proxy.config import Settings
from bugzilla_proxy.scope import AUTH_FIELDS, Grant, Scope
from bugzilla_proxy.tokens import KeySourceUnavailable, TokenError, TokenVerifier
from bugzilla_proxy.upstream import Upstream, UpstreamError

log = logging.getLogger(__name__)

# 101 and 102 are what agent_tools.bugzilla already renders as
# `endpoint_not_exposed` and `access_denied`.
CODE_NOT_EXPOSED = 101
CODE_ACCESS_DENIED = 102
CODE_UPSTREAM = 103
# The request asks for something this proxy will not do as written. The
# message says what to change: the proxy refuses rather than quietly adjusting
# a request, so a caller never gets an answer to a question it did not ask.
CODE_INVALID_REQUEST = 104


class RequestRejected(Exception):
    """A request the proxy refuses as written, with a message saying why."""

    def __init__(
        self,
        message: str,
        code: int = CODE_INVALID_REQUEST,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


# Credentials a caller might try to smuggle past our header swap. Refused
# rather than dropped, so a caller that put its token in the wrong place is
# told so instead of seeing an unexplained authorization failure.
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

# The only query parameters each per-bug endpoint accepts. BMO merges query
# parameters into the call its path maps to, so anything else can widen it:
# `bug/1/comment?ids=2` returns bug 2's comments too, `comment_ids` pulls
# comments from any bug, and `bug/attachment/1?ids=2` adds every attachment on
# bug 2. An allowlist rather than a denylist, so a parameter BMO adds later is
# refused until someone has looked at it. A comment request's `ids` is
# accepted but never forwarded as given: each bug in it is authorized first.
_COMMENT_PARAMS = frozenset({"new_since", "include_fields", "exclude_fields", "ids"})
_ATTACHMENT_PARAMS = frozenset({"include_fields", "exclude_fields"})

# Fields BMO will inline into a bug when asked, each mapped to where to read it
# instead. They have their own endpoints, with their own authorization, flags
# and private-content filtering, so search refuses them: otherwise a token
# without comment access, or with `attachments` off, could read both through
# search. `description` is comment 0's text. Verified on bugzilla.mozilla.org.
_INLINE_EXTRAS = {
    "comments": "read comments from /rest/bug/{id}/comment",
    "description": "it is the first comment, so read it from /rest/bug/{id}/comment",
    "attachments": "read attachments from /rest/bug/{id}/attachment",
    "history": "bug history is not exposed by this proxy",
}

# Group names `include_fields` accepts. They mean "widen", so they can never
# narrow a projection.
_FIELD_GROUPS = frozenset({"_default", "_all", "_extra", "_custom"})

# Groups BMO expands to include the inline extras above, so refused with them.
_WIDENING_GROUPS = frozenset({"_all", "_extra"})

# Phrases marking an upstream complaint about *our* credential. The exact list
# also matters to clients, which read it to tell an auth failure from an
# ordinary one; see tests/test_error_contract.py before changing it.
_CREDENTIAL_PHRASES = ("API key", "username or password")

# A numbered search condition (`f1`, `o1`, `v1`, `n1`, `j1`), as BMO's boolean
# charts name them.
_CHART_PARAM = re.compile(r"^[fovnj](\d+)$")

_BUG_COMMENTS = re.compile(r"^bug/(\d+)/comment$")
_BUG_ATTACHMENTS = re.compile(r"^bug/(\d+)/attachment$")
_ATTACHMENT = re.compile(r"^bug/attachment/(\d+)$")

_ENDPOINT_BUG = "bug"
_ENDPOINT_COMMENTS = "bug/*/comment"
_ENDPOINT_ATTACHMENTS = "bug/*/attachment"
_ENDPOINT_ATTACHMENT = "bug/attachment/*"


def _error(code: int, message: str, status_code: int = 404) -> JSONResponse:
    """Render a Bugzilla-shaped error.

    An upstream complaint about credentials is replaced rather than relayed: it
    is about the proxy's own credential, not the caller's token, so passing it
    on would both mislead the caller and report our credential state to them.
    """
    if any(phrase in message for phrase in _CREDENTIAL_PHRASES):
        message = "Bugzilla rejected the proxy's credentials"
    return JSONResponse(
        {"error": True, "code": code, "message": message},
        status_code=status_code,
    )


def _field_names(raw: Any) -> set[str]:
    return {name.strip() for name in str(raw or "").split(",") if name.strip()}


def _bug_denied(bug_id: int) -> JSONResponse:
    return _error(
        CODE_ACCESS_DENIED,
        f"Bug {bug_id} does not exist or is outside this run's Bugzilla scope.",
    )


def _attachment_denied(attachment_id: int) -> JSONResponse:
    return _error(
        CODE_ACCESS_DENIED,
        f"Attachment {attachment_id} does not exist or is outside this run's "
        "Bugzilla scope.",
    )


def _requested_fields(params: dict[str, Any], scope: Scope) -> frozenset[str] | None:
    """The caller's ``include_fields`` for a search, or None for no narrowing.

    Refuses anything the token can never return, rather than leaving it out of
    the response unexplained. A group name like ``_default`` asks to widen, so
    it counts as no narrowing, but only where some grant serves whole bugs.
    """
    names = _field_names(params.get("include_fields"))

    extras = sorted(names & set(_INLINE_EXTRAS))
    if extras:
        raise RequestRejected(
            "Search does not return "
            + "; ".join(f"'{name}': {_INLINE_EXTRAS[name]}" for name in extras)
            + "."
        )
    widening = sorted(names & _WIDENING_GROUPS)
    if widening:
        raise RequestRejected(
            f"The include_fields group(s) {', '.join(widening)} are not supported, "
            "because they include comments, attachments and history. List the "
            "fields you need, or use _default."
        )

    groups = names & _FIELD_GROUPS
    fields = names - _FIELD_GROUPS
    exposed = scope.exposed_fields(_ENDPOINT_BUG)
    if exposed is not None:
        if groups:
            raise RequestRejected(
                f"This token cannot return the {', '.join(sorted(groups))} field "
                f"set. It exposes only: {', '.join(sorted(exposed))}. List the "
                "fields you need in include_fields."
            )
        hidden = sorted(fields - exposed)
        if hidden:
            raise RequestRejected(
                f"This token cannot return {', '.join(hidden)}. It exposes only: "
                f"{', '.join(sorted(exposed))}."
            )

    if not fields or groups:
        return None
    return frozenset(fields)


def _add_public_only_condition(params: dict[str, Any]) -> None:
    """Ask BMO for bugs in no security group, on a search that can see no other.

    An efficiency, not the guard: it lets BMO drop private bugs before the
    limit applies, so a page comes back full instead of thinned out by the
    proxy's own filtering. The caller controls the rest of the search, and
    `j_top=OR`, a negated or unclosed group can neutralize this condition
    (verified on bugzilla.mozilla.org), so every result is still checked
    against the grants; a caller that does so only loses the efficiency.
    Numbered after the caller's highest condition, so it cannot replace one.
    """
    used = [int(m.group(1)) for key in params if (m := _CHART_PARAM.match(key))]
    n = max(used, default=0) + 1
    params[f"f{n}"] = "bug_group"
    params[f"o{n}"] = "isempty"


def _wants_field(params: dict[str, Any], name: str) -> bool:
    """Whether the caller's own include/exclude lists would return ``name``."""
    include = _field_names(params.get("include_fields"))
    if include and name not in include and not include & _FIELD_GROUPS:
        return False
    return name not in _field_names(params.get("exclude_fields"))


# Parameters the proxy reads as comma-separated lists. Repeats are joined, so
# `include_fields=a&include_fields=b` means the same as `include_fields=a,b`.
_LIST_PARAMS = frozenset({"include_fields", "exclude_fields", "ids"})


def _collect_params(request: Request, keep: Callable[[str], bool]) -> dict[str, Any]:
    """The caller's query params for which ``keep(name)`` holds.

    Repeated parameters keep every value: BMO search reads `product=A&product=B`
    as either product, and keeping only the last would quietly narrow it.
    """
    collected: dict[str, list[str]] = {}
    for key, value in request.query_params.multi_items():
        if keep(key):
            collected.setdefault(key, []).append(value)
    params: dict[str, Any] = {}
    for key, values in collected.items():
        if key in _LIST_PARAMS:
            params[key] = ",".join(v for v in values if v)
        elif len(values) == 1:
            params[key] = values[0]
        else:
            params[key] = values
    return params


def _reject_credentials(request: Request) -> None:
    """Refuse a credential in the query string, wherever it is sent."""
    smuggled = sorted(set(request.query_params) & _AUTH_PARAMS)
    if smuggled:
        raise RequestRejected(
            f"Credentials are not accepted as query parameters "
            f"({', '.join(smuggled)}). Send the run's token in the "
            "X-Bugzilla-API-Key header and nothing else."
        )


def _all_params(request: Request) -> dict[str, Any]:
    """Every query param the caller sent, for search, which filters results."""
    return _collect_params(request, lambda key: True)


def _allowed_params(
    request: Request, allowed: frozenset[str], endpoint: str
) -> dict[str, Any]:
    """The caller's query params, refusing any outside ``allowed``."""
    unsupported = sorted(set(request.query_params) - allowed)
    if unsupported:
        raise RequestRejected(
            f"Unsupported parameter(s) on /{endpoint}: {', '.join(unsupported)}. "
            f"Supported: {', '.join(sorted(allowed))}."
        )
    return _collect_params(request, lambda key: key in allowed)


def _extra_bug_ids(request: Request) -> list[int] | None:
    """The bug ids in a comment request's ``ids``, or None if one is malformed.

    Accepts both repeated and comma-separated forms, as BMO does.
    """
    ids: list[int] = []
    for raw in request.query_params.getlist("ids"):
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if not part.isdigit():
                return None
            ids.append(int(part))
    return ids


def _requiring_is_private(params: dict[str, Any]) -> dict[str, Any]:
    """``params`` with ``is_private`` guaranteed in the response.

    The caller's ``include_fields`` or ``exclude_fields`` could otherwise drop
    it, and its absence is treated as private, so this keeps a caller's
    narrowing from hiding comments it is entitled to.
    """
    params = dict(params)
    include = params.get("include_fields")
    if include:
        names = [n.strip() for n in str(include).split(",") if n.strip()]
        if "is_private" not in names:
            names.append("is_private")
        params["include_fields"] = ",".join(names)
    exclude = params.get("exclude_fields")
    if exclude:
        names = [
            n.strip()
            for n in str(exclude).split(",")
            if n.strip() and n.strip() != "is_private"
        ]
        if names:
            params["exclude_fields"] = ",".join(names)
        else:
            params.pop("exclude_fields")
    return params


def _is_public_record(record: Any) -> bool:
    """True if a comment or attachment is explicitly not private.

    Missing counts as private: we always ask for the field, so its absence
    means something went wrong, and guessing is how a private comment leaks.
    """
    return isinstance(record, dict) and record.get("is_private") in (False, 0)


def _as_requested(records: list[Any], params: dict[str, Any]) -> list[Any]:
    """``records`` without ``is_private`` if the caller did not ask for it.

    The proxy adds the field to the upstream request for its own filtering;
    the caller should get back the fields it asked for and no others.
    """
    if _wants_field(params, "is_private"):
        return records
    return [
        {k: v for k, v in r.items() if k != "is_private"} if isinstance(r, dict) else r
        for r in records
    ]


def _visible_records(records: Any, grant: Grant) -> list[Any]:
    """The comments or attachments ``grant`` may see.

    Private comments and attachments are visible upstream to insiders whatever
    the bug's own groups, so only a grant cleared for private data, which in
    turn requires a confidential run, gets them.
    """
    if not isinstance(records, list):
        return []
    if grant.is_private:
        return records
    return [r for r in records if _is_public_record(r)]


def _only_keys(mapping: Any, keys: set[str]) -> dict[str, Any]:
    """``mapping`` restricted to ``keys``, comparing as strings.

    BMO keys these maps by id, as a JSON string.
    """
    if not isinstance(mapping, dict):
        return {}
    return {str(k): v for k, v in mapping.items() if str(k) in keys}


_PRIVILEGED = "privileged"
_UNPRIVILEGED = "unprivileged"


def _key_for(scope: Scope) -> str:
    """Which key reads for ``scope``: the unprivileged one unless it must not.

    A scope that cannot reach private bugs reads with a key from an account in
    no groups, so BMO refuses anything private even if a check here were
    wrong. Only a scope with a private grant needs, and gets, the privileged
    key, and stays behind every check the proxy applies.
    """
    return _PRIVILEGED if scope.is_private else _UNPRIVILEGED


@dataclass(frozen=True)
class Backend:
    """One upstream credential, and what it has told us about each bug.

    Kept per credential: the two keys see different bugs, so an answer cached
    from one must never decide a request served with the other.
    """

    upstream: Upstream
    bug_cache: TTLCache


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
    if (
        settings.bugzilla_privileged_api_key
        and settings.bugzilla_privileged_api_key
        == settings.bugzilla_unprivileged_api_key
    ):
        raise ValueError(
            "the privileged and unprivileged Bugzilla API keys are the same; "
            "scopes without private grants must read with a key from an "
            "account in no groups, so a bug in the proxy cannot reach private "
            "data"
        )

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

    def backend(given: Upstream | None, key: str) -> Backend | None:
        upstream = given or (Upstream(settings, key) if key else None)
        if upstream is None:
            return None
        # Keyed by bug id, not by token: what this credential sees of a bug
        # is the same for every caller using it. The TTL bounds how long a
        # newly-private bug keeps being served.
        cache = TTLCache(
            maxsize=settings.decision_cache_max_entries,
            ttl=settings.decision_cache_ttl_seconds,
        )
        return Backend(upstream=upstream, bug_cache=cache)

    # Keyed by whether the token's scope reaches private bugs. Either may be
    # missing; a token needing it gets a 503.
    app.state.backends = {
        _PRIVILEGED: backend(privileged_upstream, settings.bugzilla_privileged_api_key),
        _UNPRIVILEGED: backend(
            unprivileged_upstream, settings.bugzilla_unprivileged_api_key
        ),
    }

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint for Cloud Run."""
        return {"message": "Service is healthy", "status": "ok"}

    async def _auth_fields_for_many(
        request: Request, bug_ids: list[int]
    ) -> dict[int, dict[str, Any] | None]:
        """Fetch just enough of each bug to decide access, memoised.

        One upstream call covers every id not already cached. An id upstream
        does not return maps to None.
        """
        backend: Backend = request.state.backend
        cache = backend.bug_cache
        missing = sorted({i for i in bug_ids if i not in cache})
        if missing:
            payload = await backend.upstream.get(
                "bug",
                {
                    "id": ",".join(str(i) for i in missing),
                    "include_fields": ",".join(sorted(AUTH_FIELDS)),
                },
            )
            found: dict[int, dict[str, Any]] = {}
            for bug in payload.get("bugs") or []:
                try:
                    found[int(bug["id"])] = bug
                except KeyError, TypeError, ValueError:
                    continue
            for bug_id in missing:
                cache[bug_id] = found.get(bug_id)
        return {bug_id: cache.get(bug_id) for bug_id in bug_ids}

    async def _auth_fields_for(request: Request, bug_id: int) -> dict[str, Any] | None:
        return (await _auth_fields_for_many(request, [bug_id]))[bug_id]

    def _audit(scope: Scope, event: str, **fields: Any) -> None:
        # The `audit` prefix is what separates the access trail from ordinary
        # logging by this module; the service name is already on every line as
        # the logger name.
        log.info(
            "audit %s run=%s agent=%s requested_by=%s %s",
            event,
            scope.run_id,
            scope.agent,
            scope.requested_by or "-",
            " ".join(f"{k}={v}" for k, v in fields.items()),
        )

    async def _authenticate(request: Request) -> Scope:
        token = request.headers.get("X-Bugzilla-API-Key", "")
        # In a thread: fetching the issuer's keys is a blocking HTTP call.
        return await asyncio.to_thread(request.app.state.verifier.verify, token)

    def _search_limit(raw: Any) -> int:
        """The row limit to send upstream, or a rejection explaining the range.

        Absent means the ceiling, as BMO applies its own default when none is
        given. Anything else must already be in range: capping a larger value
        would hand back fewer rows than asked for without saying so, and BMO
        reads 0 as "no limit", which this proxy does not offer.
        """
        ceiling = settings.max_search_limit
        if raw is None or raw == "":
            return ceiling
        if isinstance(raw, list):
            raise RequestRejected("Give the 'limit' parameter only once.")
        try:
            limit = int(raw)
        except TypeError, ValueError:
            raise RequestRejected(
                f"The 'limit' parameter must be a whole number from 1 to {ceiling}."
            ) from None
        if not 1 <= limit <= ceiling:
            raise RequestRejected(
                f"The 'limit' parameter must be from 1 to {ceiling}, got {limit}. "
                "Use 'offset' to page through more results."
            )
        return limit

    async def _search(request: Request, scope: Scope) -> JSONResponse:
        if not any(g.allows_endpoint(_ENDPOINT_BUG) for g in scope.grants):
            return _error(CODE_NOT_EXPOSED, "This proxy does not expose bug search.")

        params = _all_params(request)
        requested = _requested_fields(params, scope)

        upstream_fields = scope.upstream_fields(requested)
        if upstream_fields:
            params["include_fields"] = ",".join(sorted(upstream_fields))
        else:
            params.pop("include_fields", None)

        # The caller may exclude fields the decision needs. Those are still
        # fetched, and only taken off the response, so excluding `groups` hides
        # the field rather than quietly denying every bug.
        excluded = _field_names(params.pop("exclude_fields", None))
        if excluded - AUTH_FIELDS:
            params["exclude_fields"] = ",".join(sorted(excluded - AUTH_FIELDS))

        params["limit"] = str(_search_limit(params.get("limit")))
        if not scope.is_private:
            _add_public_only_condition(params)

        payload = await request.state.backend.upstream.get("bug", params)

        visible: list[dict[str, Any]] = []
        for bug in payload.get("bugs") or []:
            grant = scope.grant_for_endpoint(bug, _ENDPOINT_BUG)
            if grant is not None:
                served = grant.project(bug, requested)
                # Refused up front; dropped again in case upstream sends them.
                for name in (*_INLINE_EXTRAS, *excluded):
                    served.pop(name, None)
                visible.append(served)

        returned = len(payload.get("bugs") or [])
        _audit(
            scope,
            "search",
            upstream=returned,
            visible=len(visible),
            ids=",".join(str(b.get("id")) for b in visible) or "-",
        )
        return JSONResponse({"bugs": visible})

    def _decide(
        scope: Scope, bug_id: int, bug: dict[str, Any] | None, endpoint: str
    ) -> tuple[Grant | None, JSONResponse | None]:
        """The grant covering ``endpoint`` on ``bug``, or a denial.

        Every denial gets the same answer, whatever the reason: nonexistent,
        in a group the token lacks, outside the anchor, or at a tier without
        this endpoint. The proxy's credential may see more than the caller
        could, so naming the reason would report what that credential saw.
        The reason goes to the audit log only.
        """
        if bug is None:
            _audit(scope, "deny", bug=bug_id, endpoint=endpoint, reason="not_found")
            return None, _bug_denied(bug_id)
        grant = scope.grant_for_endpoint(bug, endpoint)
        if grant is None:
            _audit(scope, "deny", bug=bug_id, endpoint=endpoint, reason="out_of_scope")
            return None, _bug_denied(bug_id)
        _audit(scope, "allow", bug=bug_id, endpoint=endpoint, tier=grant.tier)
        return grant, None

    async def _authorized_grant(
        request: Request, scope: Scope, bug_id: int, endpoint: str
    ) -> tuple[Grant | None, JSONResponse | None]:
        bug = await _auth_fields_for(request, bug_id)
        return _decide(scope, bug_id, bug, endpoint)

    async def _comments(request: Request, scope: Scope, bug_id: int) -> JSONResponse:
        """Comments for the bug in the path, plus any in ``ids``.

        Each bug is authorized on its own, and one the run may not see fails
        the whole request, naming it. That matches BMO, which fails the call
        on an inaccessible id rather than leaving it out, so the caller learns
        nothing it would not learn from BMO directly.
        """
        params = _allowed_params(request, _COMMENT_PARAMS, f"bug/{bug_id}/comment")
        params.pop("ids", None)
        extra = _extra_bug_ids(request)
        if extra is None:
            raise RequestRejected(
                "The 'ids' parameter must be a comma-separated list of bug ids."
            )
        if len(extra) + 1 > settings.max_search_limit:
            raise RequestRejected(
                f"A comment request may cover at most {settings.max_search_limit} "
                f"bugs, and this one names {len(extra) + 1}. Split it into "
                "smaller requests."
            )

        wanted = [bug_id, *(i for i in dict.fromkeys(extra) if i != bug_id)]
        bugs = await _auth_fields_for_many(request, wanted)

        _grant, denial = _decide(scope, bug_id, bugs[bug_id], _ENDPOINT_COMMENTS)
        if denial is not None:
            return denial
        grants = {str(bug_id): _grant}
        denied: list[int] = []
        for other in wanted[1:]:
            grant, _denial = _decide(scope, other, bugs[other], _ENDPOINT_COMMENTS)
            if grant is None:
                denied.append(other)
            else:
                grants[str(other)] = grant
        if denied:
            listed = ", ".join(str(i) for i in denied)
            return _error(
                CODE_ACCESS_DENIED,
                f"Bug(s) {listed} in 'ids' do not exist or are outside this "
                "run's Bugzilla scope. Remove them and retry.",
            )

        upstream_params = _requiring_is_private(params)
        others = [key for key in grants if key != str(bug_id)]
        if others:
            upstream_params["ids"] = ",".join(others)
        payload = await request.state.backend.upstream.get(
            f"bug/{bug_id}/comment", upstream_params
        )
        # Only the authorized bugs, and never the `comments` map, which is keyed
        # by comment id and is how `comment_ids` results come back.
        served: dict[str, Any] = {}
        for key, entry in _only_keys(payload.get("bugs"), set(grants)).items():
            entry = dict(entry) if isinstance(entry, dict) else {}
            entry["comments"] = _as_requested(
                _visible_records(entry.get("comments"), grants[key]), params
            )
            served[key] = entry
        return JSONResponse({"bugs": served})

    async def _attachments(request: Request, scope: Scope, bug_id: int) -> JSONResponse:
        if not scope.attachments:
            return _error(
                CODE_NOT_EXPOSED, "This run may not read Bugzilla attachments."
            )
        params = _allowed_params(
            request, _ATTACHMENT_PARAMS, f"bug/{bug_id}/attachment"
        )
        grant, denial = await _authorized_grant(
            request, scope, bug_id, _ENDPOINT_ATTACHMENTS
        )
        if denial is not None:
            return denial
        payload = await request.state.backend.upstream.get(
            f"bug/{bug_id}/attachment", _requiring_is_private(params)
        )
        # Only the authorized bug, and never the `attachments` map, which is
        # keyed by attachment id and is how `attachment_ids` results come back.
        records = _only_keys(payload.get("bugs"), {str(bug_id)}).get(str(bug_id))
        served = _as_requested(_visible_records(records, grant), params)
        return JSONResponse({"bugs": {str(bug_id): served}})

    async def _attachment(
        request: Request, scope: Scope, attachment_id: int
    ) -> Response:
        """One attachment by its own id.

        Its bug is only discoverable from the attachment, so this first reads
        just enough of it to decide, then streams the full response as BMO
        sends it. Attachment data can be large and is never held in memory.
        Nothing is requested beyond the metadata until the bug clears.
        """
        if not scope.attachments:
            return _error(
                CODE_NOT_EXPOSED, "This run may not read Bugzilla attachments."
            )
        path = f"bug/attachment/{attachment_id}"
        params = _allowed_params(request, _ATTACHMENT_PARAMS, path)
        upstream = request.state.backend.upstream

        def denied(reason: str) -> JSONResponse:
            _audit(
                scope,
                "deny",
                attachment=attachment_id,
                endpoint=_ENDPOINT_ATTACHMENT,
                reason=reason,
            )
            return _attachment_denied(attachment_id)

        try:
            meta = await upstream.get(path, {"include_fields": "id,bug_id,is_private"})
        except UpstreamError as exc:
            if exc.status_code >= 500:
                raise
            # BMO refusing the proxy's own credential is one more reason the
            # attachment is unavailable, and gets the same answer as the rest.
            return denied("upstream_refused")
        record = _only_keys(meta.get("attachments"), {str(attachment_id)}).get(
            str(attachment_id)
        )
        bug_id = (record or {}).get("bug_id")
        if bug_id is None:
            return denied("not_found")
        grant, denial = await _authorized_grant(
            request, scope, int(bug_id), _ENDPOINT_ATTACHMENT
        )
        if denial is not None:
            # Answered as the attachment, not its bug: naming the bug would
            # reveal which bug a hidden attachment belongs to.
            return _attachment_denied(attachment_id)
        if not _visible_records([record], grant):
            return denied("private_attachment")

        # Cleared. Relayed unparsed, so what keeps the body to this one
        # attachment is the parameter allowlist: `ids` and `attachment_ids`,
        # which would add others, are refused above.
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

    @app.get("/rest/{path:path}")
    async def rest(path: str, request: Request) -> Response:
        try:
            scope = await _authenticate(request)
        except KeySourceUnavailable as exc:
            log.warning("Could not verify a request to %s: %s", path, exc)
            return _error(CODE_UPSTREAM, "Token verification is unavailable.", 503)
        except TokenError as exc:
            log.warning("Rejected request to %s: %s", path, exc)
            return _error(
                CODE_ACCESS_DENIED, f"This run is not authorized: {exc}.", 401
            )

        route = path.strip("/")
        key_name = _key_for(scope)
        backend = request.app.state.backends.get(key_name)
        if backend is None:
            log.error("No %s Bugzilla API key configured", key_name)
            reach = "with" if key_name == _PRIVILEGED else "without"
            return _error(
                CODE_UPSTREAM,
                f"This proxy has no {key_name} Bugzilla key configured, so it "
                f"cannot serve tokens {reach} private grants.",
                503,
            )
        request.state.backend = backend

        try:
            _reject_credentials(request)
            if route == "version":
                # Brokers probe this at startup to check URL and key. It is not
                # bug data, so any valid token may read it, and only the
                # version string is passed on.
                payload = await request.state.backend.upstream.get("version", {})
                return JSONResponse({"version": payload.get("version")})
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
        except RequestRejected as exc:
            _audit(scope, "reject", endpoint=route, code=exc.code)
            return _error(exc.code, str(exc), exc.status_code)
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
