"""Read-only Bugzilla tools backed by bugsy.

Framework-neutral: each tool is a ``@tool``-decorated handler whose first
parameter is a :class:`BugzillaContext`. Handlers return plain data and surface
proxy-level restrictions (code 101: endpoint not exposed, code 102: access
denied) as a structured :class:`~agent_tools.registry.ToolError`.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Annotated, Any

import bugsy
from pydantic import Field

from agent_tools.registry import ToolError, tool, tools_in


@dataclass
class BugzillaContext:
    """Holds the live bugsy client.

    Every tool receives the same instance, so they share auth and one TCP
    connection pool.
    """

    client: bugsy.Bugsy


def _bugsy_error(e: bugsy.BugsyException) -> ToolError:
    """Turn a bugsy exception into a structured ToolError.

    The payload is friendly and machine-parseable so the agent can decide what
    to do (skip the bug, try a different endpoint, ...) rather than just seeing
    a stack trace.
    """
    code = getattr(e, "code", None)
    msg = getattr(e, "msg", str(e))
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
    try:
        result = ctx.client.request("bug", params=params)
    except bugsy.BugsyException as e:
        raise _bugsy_error(e) from e
    bugs = result.get("bugs", [])
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
    id_csv = ",".join(str(i) for i in ids)
    try:
        result = ctx.client.request(
            "bug", params={"id": id_csv, "include_fields": include}
        )
    except bugsy.BugsyException as e:
        raise _bugsy_error(e) from e
    bugs = result.get("bugs", [])
    returned = {b["id"] for b in bugs}
    inaccessible = [i for i in ids if i not in returned]

    payload = {"count": len(bugs), "bugs": bugs, "inaccessible": inaccessible}

    if include_comments and bugs:
        # Bugzilla lets us fetch comments for many bugs in one call by hitting
        # /bug/{first}/comment?ids=rest. One extra round trip total.
        first, *rest = [b["id"] for b in bugs]
        cparams = {"ids": ",".join(str(i) for i in rest)} if rest else {}
        try:
            cres = ctx.client.request(f"bug/{first}/comment", params=cparams)
            comments_by_bug = {
                int(bid): data["comments"] for bid, data in cres.get("bugs", {}).items()
            }
            for b in bugs:
                b["comments"] = comments_by_bug.get(b["id"], [])
        except bugsy.BugsyException as e:
            payload["comments_error"] = {
                "code": getattr(e, "code", None),
                "message": getattr(e, "msg", str(e)),
            }

    return payload


@tool
async def get_bug_comments(
    ctx: BugzillaContext,
    bug_id: Annotated[int, Field(description="Bug ID.")],
) -> dict:
    """Fetch all comments for a single bug."""
    try:
        result = ctx.client.request(f"bug/{bug_id}/comment")
    except bugsy.BugsyException as e:
        raise _bugsy_error(e) from e
    comments = result.get("bugs", {}).get(str(bug_id), {}).get("comments", [])
    return {"bug_id": bug_id, "count": len(comments), "comments": comments}


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
    params = {} if include_data else {"exclude_fields": "data"}
    try:
        result = ctx.client.request(f"bug/{bug_id}/attachment", params=params)
    except bugsy.BugsyException as e:
        raise _bugsy_error(e) from e
    atts = result.get("bugs", {}).get(str(bug_id), [])
    return {"bug_id": bug_id, "count": len(atts), "attachments": atts}


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
    try:
        result = ctx.client.request(f"bug/attachment/{attachment_id}")
    except bugsy.BugsyException as e:
        raise _bugsy_error(e) from e

    att = result.get("attachments", {}).get(str(attachment_id))
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
