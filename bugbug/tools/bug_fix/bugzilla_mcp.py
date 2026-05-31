"""In-process MCP server wrapping bugsy for Bugzilla REST access.

Exposes read-only tools to a Claude agent. Write actions are recorded
via the in-process ``actions`` MCP server built from the framework-agnostic
registry in ``hackbot_runtime.actions`` (see
``hackbot_runtime/actions/claude_sdk.py``), so the broker holds the Bugzilla
API key but has no write capability at all.
All tools gracefully handle proxy-level restrictions (code 101:
endpoint not exposed, code 102: access denied).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

import bugsy
from claude_agent_sdk import create_sdk_mcp_server, tool

# --------------------------------------------------------------------------- #
# Shared context
# --------------------------------------------------------------------------- #


@dataclass
class BugzillaContext:
    """Holds the live bugsy client.

    The MCP tool functions close over a single instance of this class so
    they share auth and one TCP connection pool.
    """

    client: bugsy.Bugsy


def _text(content: str) -> dict:
    """Wrap plain text in MCP content format."""
    return {"content": [{"type": "text", "text": content}]}


def _jtext(obj) -> dict:
    """Serialise an object to pretty JSON inside MCP text content."""
    return _text(json.dumps(obj, indent=2, default=str))


def _handle_bugsy_error(e: bugsy.BugsyException) -> dict:
    """Turn a bugsy exception into a structured tool error response.

    We deliberately return ``is_error: True`` but with a friendly,
    machine-parseable payload so the agent can decide what to do
    (skip the bug, try a different endpoint, etc) rather than just
    seeing a stack trace.
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
    payload = {"error": kind, "code": code, "message": msg}
    if hint:
        payload["hint"] = hint
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
        "is_error": True,
    }


# --------------------------------------------------------------------------- #
# Server factory
# --------------------------------------------------------------------------- #


def build_server(ctx: BugzillaContext):
    """Create and return the in-process MCP server bound to ``ctx``.

    All tool functions are closures over ``ctx`` so they share the same
    bugsy session (one TCP connection pool, one auth header).
    """
    # ----- READ TOOLS -------------------------------------------------- #

    @tool(
        "search_bugs",
        "Search Bugzilla using raw REST query parameters. Returns matching "
        "bugs in one bulk request. Parameters are ANDed together (intersect). "
        "IMPORTANT: this proxy drops 'whiteboard' and 'keywords' from _all / "
        "_default field sets — list them explicitly in include_fields if you "
        "need them. Common params: id, keywords, blocks, depends_on, product, "
        "component, status, resolution, priority, severity, assigned_to, "
        "whiteboard, include_fields, limit.",
        {
            "type": "object",
            "properties": {
                "params": {
                    "type": "object",
                    "description": (
                        "Bugzilla REST /bug query parameters. Values may be "
                        "strings, ints, or comma-separated lists. Example: "
                        '{"blocks": 12345, "keywords": "sec-low", '
                        '"include_fields": "id,summary,status,whiteboard,keywords"}'
                    ),
                    "additionalProperties": True,
                }
            },
            "required": ["params"],
        },
    )
    async def search_bugs(args):
        params = args["params"]
        try:
            result = ctx.client.request("bug", params=params)
        except bugsy.BugsyException as e:
            return _handle_bugsy_error(e)
        bugs = result.get("bugs", [])
        return _jtext({"count": len(bugs), "bugs": bugs})

    @tool(
        "get_bugs",
        "Fetch one or more bugs by ID in a single bulk request. "
        "Inaccessible bugs are silently dropped by the proxy — this tool "
        "diffs requested vs returned and reports them under 'inaccessible'. "
        "Remember: request 'whiteboard' and 'keywords' explicitly in "
        "include_fields if you need them.",
        {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Bug IDs to fetch.",
                },
                "include_fields": {
                    "type": "string",
                    "description": (
                        "Comma-separated field list, or '_default'/'_all'. "
                        "Defaults to a sensible triage set."
                    ),
                },
                "include_comments": {
                    "type": "boolean",
                    "description": (
                        "If true, also bulk-fetch comments (one extra request "
                        "total, not one per bug)."
                    ),
                },
            },
            "required": ["ids"],
        },
    )
    async def get_bugs(args):
        ids = args["ids"]
        if not ids:
            return _jtext({"count": 0, "bugs": [], "inaccessible": []})
        include = args.get("include_fields") or (
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
            return _handle_bugsy_error(e)
        bugs = result.get("bugs", [])
        returned = {b["id"] for b in bugs}
        inaccessible = [i for i in ids if i not in returned]

        payload = {
            "count": len(bugs),
            "bugs": bugs,
            "inaccessible": inaccessible,
        }

        if args.get("include_comments") and bugs:
            # Bugzilla lets us fetch comments for many bugs in one call by
            # hitting /bug/{first}/comment?ids=rest. One extra round trip
            # total regardless of bug count.
            first, *rest = [b["id"] for b in bugs]
            cparams = {"ids": ",".join(str(i) for i in rest)} if rest else {}
            try:
                cres = ctx.client.request(f"bug/{first}/comment", params=cparams)
                # Response keys bugs by string ID.
                comments_by_bug = {
                    int(bid): data["comments"]
                    for bid, data in cres.get("bugs", {}).items()
                }
                for b in bugs:
                    b["comments"] = comments_by_bug.get(b["id"], [])
            except bugsy.BugsyException as e:
                payload["comments_error"] = {
                    "code": getattr(e, "code", None),
                    "message": getattr(e, "msg", str(e)),
                }

        return _jtext(payload)

    @tool(
        "get_bug_comments",
        "Fetch all comments for a single bug.",
        {"bug_id": int},
    )
    async def get_bug_comments(args):
        bug_id = args["bug_id"]
        try:
            result = ctx.client.request(f"bug/{bug_id}/comment")
        except bugsy.BugsyException as e:
            return _handle_bugsy_error(e)
        comments = result.get("bugs", {}).get(str(bug_id), {}).get("comments", [])
        return _jtext({"bug_id": bug_id, "count": len(comments), "comments": comments})

    @tool(
        "get_bug_attachments",
        "Fetch attachments for a bug. By default returns metadata only "
        "(cheap, safe for large binaries). Set include_data=true to also "
        "download the content — Bugzilla returns it base64-encoded in the "
        "'data' field of each attachment.",
        {
            "type": "object",
            "properties": {
                "bug_id": {"type": "integer"},
                "include_data": {
                    "type": "boolean",
                    "description": (
                        "If true, include base64-encoded attachment content. "
                        "Default false. Use sparingly — attachments can be large."
                    ),
                },
            },
            "required": ["bug_id"],
        },
    )
    async def get_bug_attachments(args):
        bug_id = args["bug_id"]
        params = {} if args.get("include_data") else {"exclude_fields": "data"}
        try:
            result = ctx.client.request(f"bug/{bug_id}/attachment", params=params)
        except bugsy.BugsyException as e:
            return _handle_bugsy_error(e)
        atts = result.get("bugs", {}).get(str(bug_id), [])
        return _jtext({"bug_id": bug_id, "count": len(atts), "attachments": atts})

    @tool(
        "download_attachment",
        "Fetch a single Bugzilla attachment by ID and write its decoded "
        "content to a local file. This is the inverse of add_attachment: "
        "it handles the base64 decode server-side so the agent never has "
        "to round-trip the blob through its own context. Use "
        "get_bug_attachments first to discover attachment IDs. Returns "
        "the written path, size, and content_type.",
        {
            "type": "object",
            "properties": {
                "attachment_id": {"type": "integer"},
                "dest_path": {
                    "type": "string",
                    "description": "Local filesystem path to write the "
                    "decoded attachment to. Parent directory "
                    "must already exist. Overwrites if present.",
                },
            },
            "required": ["attachment_id", "dest_path"],
        },
    )
    async def download_attachment(args):
        attachment_id = args["attachment_id"]
        dest_path = args["dest_path"]
        try:
            result = ctx.client.request(f"bug/attachment/{attachment_id}")
        except bugsy.BugsyException as e:
            return _handle_bugsy_error(e)

        att = result.get("attachments", {}).get(str(attachment_id))
        if att is None:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "error": "attachment_not_found",
                                "attachment_id": attachment_id,
                            }
                        ),
                    }
                ],
                "is_error": True,
            }

        raw = base64.b64decode(att["data"])
        with open(dest_path, "wb") as fp:
            fp.write(raw)

        return _jtext(
            {
                "attachment_id": attachment_id,
                "dest_path": dest_path,
                "size_bytes": len(raw),
                "file_name": att.get("file_name"),
                "content_type": att.get("content_type"),
            }
        )

    return create_sdk_mcp_server(
        name="bugzilla",
        version="0.1.0",
        tools=[
            search_bugs,
            get_bugs,
            get_bug_comments,
            get_bug_attachments,
            download_attachment,
        ],
    )
