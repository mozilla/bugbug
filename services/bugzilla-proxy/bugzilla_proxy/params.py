"""What a request may ask for, and what of the answer it may see.

The first half reads and validates the caller's query parameters: each check
either returns what to send upstream or raises :class:`RequestRejected` saying
what to change. Nothing is quietly dropped, capped or rewritten into a
different question. The second half cuts BMO's responses down to what the run
may see.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from fastapi import Request

from bugzilla_proxy.scope import Grant

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
AUTH_PARAMS = frozenset(
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
COMMENT_PARAMS = frozenset({"new_since", "include_fields", "exclude_fields", "ids"})
ATTACHMENT_PARAMS = frozenset({"include_fields", "exclude_fields"})

# Fields BMO will inline into a bug when asked, each mapped to where to read it
# instead. They have their own endpoints, with their own authorization, flags
# and private-content filtering, so search refuses them: otherwise a token
# without comment access, or with `attachments` off, could read both through
# search. `description` is comment 0's text. Verified on bugzilla.mozilla.org.
INLINE_EXTRAS = {
    "comments": "read comments from /rest/bug/{id}/comment",
    "description": "it is the first comment, so read it from /rest/bug/{id}/comment",
    "attachments": "read attachments from /rest/bug/{id}/attachment",
    "history": "bug history is not exposed by this proxy",
}

# Group names `include_fields` accepts. They mean "widen", so they can never
# narrow a projection.
FIELD_GROUPS = frozenset({"_default", "_all", "_extra", "_custom"})

# Groups BMO expands to include the inline extras above, so refused with them.
_WIDENING_GROUPS = frozenset({"_all", "_extra"})

# Parameters the proxy reads as comma-separated lists. Repeats are joined, so
# `include_fields=a&include_fields=b` means the same as `include_fields=a,b`.
_LIST_PARAMS = frozenset({"include_fields", "exclude_fields", "ids"})

# A numbered search condition (`f1`, `o1`, `v1`, `n1`, `j1`), as BMO's boolean
# charts name them.
_CHART_PARAM = re.compile(r"^[fovnj](\d+)$")


def field_names(raw: Any) -> set[str]:
    return {name.strip() for name in str(raw or "").split(",") if name.strip()}


def _collect(request: Request, keep: Callable[[str], bool]) -> dict[str, Any]:
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


def reject_credentials(request: Request) -> None:
    """Refuse a credential in the query string, wherever it is sent."""
    smuggled = sorted(set(request.query_params) & AUTH_PARAMS)
    if smuggled:
        raise RequestRejected(
            f"Credentials are not accepted as query parameters "
            f"({', '.join(smuggled)}). Send the run's token in the "
            "X-Bugzilla-API-Key header and nothing else."
        )


def all_params(request: Request) -> dict[str, Any]:
    """Every query param the caller sent, for search, which filters results."""
    return _collect(request, lambda key: True)


def allowed_params(
    request: Request, allowed: frozenset[str], endpoint: str
) -> dict[str, Any]:
    """The caller's query params, refusing any outside ``allowed``."""
    unsupported = sorted(set(request.query_params) - allowed)
    if unsupported:
        raise RequestRejected(
            f"Unsupported parameter(s) on /{endpoint}: {', '.join(unsupported)}. "
            f"Supported: {', '.join(sorted(allowed))}."
        )
    return _collect(request, lambda key: key in allowed)


def comment_bug_ids(request: Request, bug_id: int, ceiling: int) -> list[int]:
    """The bugs a comment request covers: the path's first, then any in ``ids``.

    Accepts both repeated and comma-separated ``ids``, as BMO does, and
    refuses a malformed one or more bugs than one request may cover.
    """
    extra: list[int] = []
    for raw in request.query_params.getlist("ids"):
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if not part.isdigit():
                raise RequestRejected(
                    "The 'ids' parameter must be a comma-separated list of bug ids."
                )
            extra.append(int(part))
    if len(extra) + 1 > ceiling:
        raise RequestRejected(
            f"A comment request may cover at most {ceiling} bugs, and this one "
            f"names {len(extra) + 1}. Split it into smaller requests."
        )
    return [bug_id, *(i for i in dict.fromkeys(extra) if i != bug_id)]


def requested_fields(
    params: dict[str, Any], exposed: frozenset[str] | None
) -> frozenset[str] | None:
    """The caller's ``include_fields`` for a search, or None for no narrowing.

    ``exposed`` is every field the token's search grants may serve, or None
    for any. Refuses anything the token can never return, rather than leaving
    it out of the response unexplained. A group name like ``_default`` asks to
    widen, so it counts as no narrowing, but only where some grant serves
    whole bugs.
    """
    names = field_names(params.get("include_fields"))

    extras = sorted(names & set(INLINE_EXTRAS))
    if extras:
        raise RequestRejected(
            "Search does not return "
            + "; ".join(f"'{name}': {INLINE_EXTRAS[name]}" for name in extras)
            + "."
        )
    widening = sorted(names & _WIDENING_GROUPS)
    if widening:
        raise RequestRejected(
            f"The include_fields group(s) {', '.join(widening)} are not supported, "
            "because they include comments, attachments and history. List the "
            "fields you need, or use _default."
        )

    groups = names & FIELD_GROUPS
    fields = names - FIELD_GROUPS
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


def search_limit(raw: Any, ceiling: int) -> int:
    """The row limit to send upstream, or a rejection explaining the range.

    Absent means the ceiling, as BMO applies its own default when none is
    given. Anything else must already be in range: capping a larger value
    would hand back fewer rows than asked for without saying so, and BMO
    reads 0 as "no limit", which this proxy does not offer.
    """
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


def add_public_only_condition(params: dict[str, Any]) -> None:
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


def wants_field(params: dict[str, Any], name: str) -> bool:
    """Whether the caller's own include/exclude lists would return ``name``."""
    include = field_names(params.get("include_fields"))
    if include and name not in include and not include & FIELD_GROUPS:
        return False
    return name not in field_names(params.get("exclude_fields"))


def requiring_is_private(params: dict[str, Any]) -> dict[str, Any]:
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


# Responses: cutting what BMO sent back down to what the run may see.


def _is_public(record: Any) -> bool:
    """True if a comment or attachment is explicitly not private.

    Missing counts as private: we always ask for the field, so its absence
    means something went wrong, and guessing is how a private comment leaks.
    """
    return isinstance(record, dict) and record.get("is_private") in (False, 0)


def visible_records(records: Any, grant: Grant) -> list[Any]:
    """The comments or attachments ``grant`` may see.

    Private comments and attachments are visible upstream to insiders whatever
    the bug's own groups, so only a grant cleared for private data, which in
    turn requires a confidential run, gets them.
    """
    if not isinstance(records, list):
        return []
    if grant.is_private:
        return records
    return [r for r in records if _is_public(r)]


def as_requested(records: list[Any], params: dict[str, Any]) -> list[Any]:
    """``records`` without ``is_private`` if the caller did not ask for it.

    The proxy adds the field to the upstream request for its own filtering;
    the caller should get back the fields it asked for and no others.
    """
    if wants_field(params, "is_private"):
        return records
    return [
        {k: v for k, v in r.items() if k != "is_private"} if isinstance(r, dict) else r
        for r in records
    ]


def only_keys(mapping: Any, keys: set[str]) -> dict[str, Any]:
    """``mapping`` restricted to ``keys``, comparing as strings.

    BMO keys these maps by id, as a JSON string.
    """
    if not isinstance(mapping, dict):
        return {}
    return {str(k): v for k, v in mapping.items() if str(k) in keys}
