"""Read-only Bugzilla tools backed by libmozdata.

Framework-neutral: each tool is a ``@tool``-decorated handler whose first
parameter is a :class:`BugzillaContext`. Handlers return plain data and surface
proxy-level restrictions (code 101: endpoint not exposed, code 102: access
denied) as a structured :class:`~agent_tools.registry.ToolError`.

libmozdata exposes no raw-request passthrough; every call goes through its
handler-based ``Bugzilla(...).get_data().wait()`` API and its configuration is
process-global (set on class attributes). :class:`BugzillaContext` applies that
global configuration on construction, which is fine because the broker is a
single-tenant sidecar holding exactly one API key and URL.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Annotated, Any

import requests
from libmozdata.bugzilla import Bugzilla, BugzillaBase
from pydantic import Field

from agent_tools.registry import ToolError, tool, tools_in


@dataclass
class BugzillaContext:
    """Carries the Bugzilla URL + API key and applies them to libmozdata.

    libmozdata reads its credentials and endpoints from class attributes rather
    than per-instance, so constructing the context configures the (process-wide)
    libmozdata ``Bugzilla`` class. Every tool then builds short-lived
    ``Bugzilla`` instances that share this auth.
    """

    api_url: str
    api_key: str

    def __post_init__(self) -> None:
        base_url = self.api_url.rstrip("/")
        BugzillaBase.TOKEN = self.api_key
        BugzillaBase.URL = base_url
        Bugzilla.API_URL = base_url + "/rest/bug"
        Bugzilla.ATTACHMENT_API_URL = Bugzilla.API_URL + "/attachment"


def _bugzilla_error(e: requests.HTTPError) -> ToolError:
    """Turn a libmozdata HTTP error into a structured ToolError.

    The payload is friendly and machine-parseable so the agent can decide what
    to do (skip the bug, try a different endpoint, ...) rather than just seeing
    a stack trace. The Bugzilla proxy reports its restrictions in the JSON body
    as ``{"code": ..., "message": ...}`` (code 101: endpoint not exposed, code
    102: access denied), which we recover from the failing response.
    """
    code = None
    msg = str(e)
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            code = body.get("code")
            msg = body.get("message", msg)

    if code == 101:
        kind = "endpoint_not_exposed"
        hint = "This Bugzilla proxy does not expose this endpoint."
    elif code == 102:
        kind = "access_denied"
        hint = "Your API key cannot access this bug. Skip it."
    else:
        kind = "bugzilla_error"
        hint = None
    payload: dict[str, Any] = {"error": kind, "code": code, "message": msg}
    if hint:
        payload["hint"] = hint
    return ToolError(msg, payload=payload)


@tool
async def search_bugs(
    ctx: BugzillaContext,
    params: Annotated[
        dict[str, Any],
        Field(
            description=(
                "Bugzilla REST /bug query parameters. Values may be strings, "
                "ints, or comma-separated lists. Example: "
                '{"blocks": 12345, "keywords": "sec-low", '
                '"include_fields": "id,summary,status,whiteboard,keywords"}'
            )
        ),
    ],
) -> dict:
    """Search Bugzilla using raw REST query parameters.

    Returns matching bugs in one bulk request. Parameters are ANDed together
    (intersect). IMPORTANT: this proxy drops 'whiteboard' and 'keywords' from
    _all / _default field sets — list them explicitly in include_fields if you
    need them. Common params: id, keywords, blocks, depends_on, product,
    component, status, resolution, priority, severity, assigned_to, whiteboard,
    include_fields, limit.
    """
    from urllib.parse import urlencode

    bugs: list[dict] = []

    def bughandler(bug: dict) -> None:
        bugs.append(bug)

    # Pass the query as a urlencoded string so libmozdata issues a single direct
    # request (its dict-query path first fires a synchronous count_only probe
    # the proxy may reject with code 101).
    query = urlencode(params, doseq=True)
    try:
        Bugzilla(query, bughandler=bughandler).get_data().wait()
    except requests.HTTPError as e:
        raise _bugzilla_error(e) from e
    return {"count": len(bugs), "bugs": bugs}


@tool
async def get_bugs(
    ctx: BugzillaContext,
    ids: Annotated[list[int], Field(description="Bug IDs to fetch.")],
    include_fields: Annotated[
        str | None,
        Field(
            description=(
                "Comma-separated field list, or '_default'/'_all'. Defaults to "
                "a sensible triage set."
            )
        ),
    ] = None,
    include_comments: Annotated[
        bool,
        Field(
            description=(
                "If true, also bulk-fetch comments (one extra request total, "
                "not one per bug)."
            )
        ),
    ] = False,
) -> dict:
    """Fetch one or more bugs by ID in a single bulk request.

    Inaccessible bugs are silently dropped by the proxy — this tool diffs
    requested vs returned and reports them under 'inaccessible'. Remember:
    request 'whiteboard' and 'keywords' explicitly in include_fields if you need
    them.
    """
    if not ids:
        return {"count": 0, "bugs": [], "inaccessible": []}
    include = include_fields or (
        "id,summary,status,resolution,product,component,priority,"
        "severity,keywords,whiteboard,assigned_to,creator,"
        "creation_time,last_change_time,blocks,depends_on,see_also,"
        "cf_crash_signature,url,version,op_sys,platform"
    )

    bugs_by_id: dict[int, dict] = {}

    def bughandler(bug: dict) -> None:
        bugs_by_id[bug["id"]] = bug

    def commenthandler(data: dict, bug_id) -> None:
        bug = bugs_by_id.get(int(bug_id))
        if bug is not None:
            bug["comments"] = data["comments"]

    kwargs: dict[str, Any] = {
        "include_fields": include.split(","),
        "bughandler": bughandler,
    }
    if include_comments:
        kwargs["commenthandler"] = commenthandler

    try:
        Bugzilla([str(i) for i in ids], **kwargs).get_data().wait()
    except requests.HTTPError as e:
        raise _bugzilla_error(e) from e

    bugs = list(bugs_by_id.values())
    returned = set(bugs_by_id)
    inaccessible = [i for i in ids if i not in returned]
    return {"count": len(bugs), "bugs": bugs, "inaccessible": inaccessible}


@tool
async def get_bug_comments(
    ctx: BugzillaContext,
    bug_id: Annotated[int, Field(description="Bug ID.")],
) -> dict:
    """Fetch all comments for a single bug."""
    collected: list[dict] = []

    def commenthandler(data: dict, _bug_id) -> None:
        collected.extend(data["comments"])

    try:
        Bugzilla([str(bug_id)], commenthandler=commenthandler).get_data().wait()
    except requests.HTTPError as e:
        raise _bugzilla_error(e) from e
    return {"bug_id": bug_id, "count": len(collected), "comments": collected}


@tool
async def get_bug_attachments(
    ctx: BugzillaContext,
    bug_id: Annotated[int, Field(description="Bug ID.")],
    include_data: Annotated[
        bool,
        Field(
            description=(
                "If true, include base64-encoded attachment content. Default "
                "false. Use sparingly — attachments can be large."
            )
        ),
    ] = False,
) -> dict:
    """Fetch attachments for a bug.

    By default returns metadata only (cheap, safe for large binaries). Set
    include_data=true to also download the content — Bugzilla returns it
    base64-encoded in the 'data' field of each attachment.
    """
    collected: list[dict] = []

    def attachmenthandler(attachments: list, _bug_id) -> None:
        collected.extend(attachments)

    # libmozdata has no exclude_fields; emulate it by requesting an explicit
    # metadata-only field set unless the caller wants the (potentially large)
    # base64 'data'.
    include_fields = (
        None
        if include_data
        else [
            "id",
            "file_name",
            "content_type",
            "size",
            "creation_time",
            "last_change_time",
            "is_patch",
            "is_obsolete",
            "is_private",
            "flags",
            "summary",
            "creator",
        ]
    )

    try:
        Bugzilla(
            [str(bug_id)],
            attachmenthandler=attachmenthandler,
            attachment_include_fields=include_fields,
        ).get_data().wait()
    except requests.HTTPError as e:
        raise _bugzilla_error(e) from e
    return {"bug_id": bug_id, "count": len(collected), "attachments": collected}


@tool
async def download_attachment(
    ctx: BugzillaContext,
    attachment_id: Annotated[
        int, Field(description="Attachment ID (discover via get_bug_attachments).")
    ],
    dest_path: Annotated[
        str,
        Field(
            description=(
                "Local filesystem path to write the decoded attachment to. "
                "Parent directory must already exist. Overwrites if present."
            )
        ),
    ],
) -> dict:
    """Fetch a Bugzilla attachment by ID and write its decoded content to a file.

    The inverse of add_attachment: it handles the base64 decode server-side so
    the agent never has to round-trip the blob through its own context. Use
    get_bug_attachments first to discover attachment IDs. Returns the written
    path, size, and content_type.
    """
    collected: list[dict] = []

    def attachmenthandler(attachments: list) -> None:
        collected.extend(attachments)

    try:
        Bugzilla(
            attachmentids=[str(attachment_id)],
            attachmenthandler=attachmenthandler,
            attachment_include_fields=["id", "data", "file_name", "content_type"],
        ).get_data().wait()
    except requests.HTTPError as e:
        raise _bugzilla_error(e) from e

    att = next((a for a in collected if a.get("id") == attachment_id), None)
    if att is None:
        raise ToolError(
            f"attachment {attachment_id} not found",
            payload={"error": "attachment_not_found", "attachment_id": attachment_id},
        )

    raw = base64.b64decode(att["data"])
    with open(dest_path, "wb") as fp:
        fp.write(raw)

    return {
        "attachment_id": attachment_id,
        "dest_path": dest_path,
        "size_bytes": len(raw),
        "file_name": att.get("file_name"),
        "content_type": att.get("content_type"),
    }


TOOLS = tools_in(__name__)
