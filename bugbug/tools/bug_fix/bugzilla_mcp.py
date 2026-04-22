"""In-process MCP server wrapping bugsy for Bugzilla REST access.

Exposes read and write tools to a Claude agent. Write tools honour
dry-run and confirm modes. All tools gracefully handle proxy-level
restrictions (code 101: endpoint not exposed, code 102: access denied).
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import sys
from dataclasses import dataclass, field

import bugsy
from claude_agent_sdk import create_sdk_mcp_server, tool

# --------------------------------------------------------------------------- #
# Shared context
# --------------------------------------------------------------------------- #


@dataclass
class BugzillaContext:
    """Holds the live bugsy client and runtime flags.

    The MCP tool functions close over a single instance of this class so
    they can share auth and honour dry-run / confirm without re-parsing
    CLI args.
    """

    client: bugsy.Bugsy
    dry_run: bool = False
    confirm: bool = False
    # Record of simulated writes during dry-run, for end-of-run summary.
    simulated: list[dict] = field(default_factory=list)


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


async def _confirm_prompt(action: str, details: dict) -> bool:
    """Interactively ask the user whether to proceed with a write.

    Runs ``input()`` in a thread so the event loop keeps turning —
    the Agent SDK's subprocess reader stays alive while we wait on
    the human. Prompt text goes to stderr so it doesn't tangle with
    the streamed agent transcript on stdout.
    """
    print(f"\n[CONFIRM] About to {action}:", file=sys.stderr)
    print(json.dumps(details, indent=2, default=str), file=sys.stderr)
    sys.stderr.flush()
    try:
        answer = await asyncio.to_thread(input, "Proceed? [y/N] ")
    except EOFError:
        answer = ""
    return answer.strip().lower() in ("y", "yes")


# --------------------------------------------------------------------------- #
# Server factory
# --------------------------------------------------------------------------- #


def build_server(ctx: BugzillaContext):
    """Create and return the in-process MCP server bound to ``ctx``.

    All tool functions are closures over ``ctx`` so they share the same
    bugsy session (one TCP connection pool, one auth header) and the same
    dry-run / confirm state.
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

    # ----- WRITE TOOLS ------------------------------------------------- #

    @tool(
        "update_bug",
        "Change fields on a bug. Accepts any field the Bugzilla REST PUT "
        "endpoint accepts: status, resolution, priority, severity, "
        "whiteboard, assigned_to, component, product, version, and the "
        "keywords / cc / see_also / depends_on / blocks set-operations "
        "({'add': [...], 'remove': [...]}). Returns Bugzilla's change "
        "report. Honours --dry-run and --confirm.",
        {
            "type": "object",
            "properties": {
                "bug_id": {"type": "integer"},
                "changes": {
                    "type": "object",
                    "description": (
                        "Field → new value. For list fields (keywords, cc, "
                        "blocks, depends_on, see_also) you may pass "
                        '{"add": [...], "remove": [...], "set": [...]}.'
                    ),
                    "additionalProperties": True,
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "Why you are making this change. Logged alongside "
                        "dry-run / confirm output so the human can audit."
                    ),
                },
            },
            "required": ["bug_id", "changes", "reasoning"],
        },
    )
    async def update_bug(args):
        bug_id = args["bug_id"]
        changes = args["changes"]
        reasoning = args["reasoning"]

        action_desc = {
            "bug_id": bug_id,
            "changes": changes,
            "reasoning": reasoning,
        }

        if ctx.dry_run:
            print(f"\n[DRY-RUN] update_bug {bug_id}", file=sys.stderr)
            print(json.dumps(action_desc, indent=2, default=str), file=sys.stderr)
            ctx.simulated.append({"action": "update_bug", **action_desc})
            return _jtext(
                {
                    "dry_run": True,
                    "would_update": bug_id,
                    "changes": changes,
                    "note": "No request sent. Re-run without --dry-run to apply.",
                }
            )

        if ctx.confirm and not await _confirm_prompt(
            f"update bug {bug_id}", action_desc
        ):
            return _jtext(
                {
                    "skipped": True,
                    "bug_id": bug_id,
                    "reason": "User declined at --confirm prompt.",
                }
            )

        try:
            result = ctx.client.request(f"bug/{bug_id}", method="PUT", json=changes)
        except bugsy.BugsyException as e:
            return _handle_bugsy_error(e)
        return _jtext({"updated": bug_id, "result": result})

    @tool(
        "add_comment",
        "Post a comment on a bug. Honours --dry-run and --confirm. "
        "Use is_private=true for security-sensitive notes.",
        {
            "type": "object",
            "properties": {
                "bug_id": {"type": "integer"},
                "text": {"type": "string"},
                "is_private": {
                    "type": "boolean",
                    "description": "Mark the comment private (security group only).",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why you are posting this comment (for audit log).",
                },
            },
            "required": ["bug_id", "text", "reasoning"],
        },
    )
    async def add_comment(args):
        bug_id = args["bug_id"]
        text = args["text"]
        is_private = bool(args.get("is_private", False))
        reasoning = args["reasoning"]

        footer = (
            "*This is an automated analysis result. If this result is incorrect "
            "please add a needinfo and feel free to correct the error.* "
        )
        text = text.rstrip() + "\n\n" + footer

        action_desc = {
            "bug_id": bug_id,
            "is_private": is_private,
            "reasoning": reasoning,
            "text": text,
        }

        if ctx.dry_run:
            print(f"\n[DRY-RUN] add_comment on bug {bug_id}", file=sys.stderr)
            print(json.dumps(action_desc, indent=2, default=str), file=sys.stderr)
            ctx.simulated.append({"action": "add_comment", **action_desc})
            return _jtext(
                {
                    "dry_run": True,
                    "would_comment_on": bug_id,
                    "text_preview": text[:200],
                    "note": "No request sent. Re-run without --dry-run to apply.",
                }
            )

        if ctx.confirm and not await _confirm_prompt(
            f"comment on bug {bug_id}", action_desc
        ):
            return _jtext(
                {
                    "skipped": True,
                    "bug_id": bug_id,
                    "reason": "User declined at --confirm prompt.",
                }
            )

        body = {"comment": text, "is_markdown": True}
        if is_private:
            body["is_private"] = True
        try:
            result = ctx.client.request(
                f"bug/{bug_id}/comment", method="POST", json=body
            )
        except bugsy.BugsyException as e:
            return _handle_bugsy_error(e)
        return _jtext({"commented_on": bug_id, "comment_id": result.get("id")})

    @tool(
        "add_attachment",
        "Upload a file as an attachment to a bug. Pass a local filesystem "
        "path — the tool reads and base64-encodes the file itself (do NOT "
        "inline file contents in the tool call). For patches, set "
        "is_patch=true and omit content_type. Honours --dry-run and "
        "--confirm.",
        {
            "type": "object",
            "properties": {
                "bug_id": {"type": "integer"},
                "file_path": {
                    "type": "string",
                    "description": "Local path to the file to upload.",
                },
                "summary": {
                    "type": "string",
                    "description": "Short description of the attachment. "
                    "Defaults to the filename.",
                },
                "content_type": {
                    "type": "string",
                    "description": "MIME type. Guessed from extension if "
                    "omitted. Ignored when is_patch=true.",
                },
                "is_patch": {
                    "type": "boolean",
                    "description": "Mark as a patch (Bugzilla forces "
                    "text/plain and enables diff view).",
                },
                "comment": {
                    "type": "string",
                    "description": "Optional comment to post alongside the "
                    "attachment. Posted as a separate "
                    "markdown-enabled comment after upload "
                    "(the attachment endpoint itself does "
                    "not support is_markdown).",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why you are attaching this (for audit log).",
                },
            },
            "required": ["bug_id", "file_path", "reasoning"],
        },
    )
    async def add_attachment(args):
        bug_id = args["bug_id"]
        file_path = args["file_path"]
        reasoning = args["reasoning"]
        is_patch = bool(args.get("is_patch", False))

        if not os.path.isfile(file_path):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"error": "file_not_found", "path": file_path}
                        ),
                    }
                ],
                "is_error": True,
            }

        file_name = os.path.basename(file_path)
        summary = args.get("summary") or file_name
        size = os.path.getsize(file_path)

        if is_patch:
            content_type = "text/plain"
        else:
            content_type = args.get("content_type") or (
                mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            )

        action_desc = {
            "bug_id": bug_id,
            "file_path": file_path,
            "file_name": file_name,
            "size_bytes": size,
            "content_type": content_type,
            "is_patch": is_patch,
            "summary": summary,
            "reasoning": reasoning,
        }

        if ctx.dry_run:
            print(f"\n[DRY-RUN] add_attachment on bug {bug_id}", file=sys.stderr)
            print(json.dumps(action_desc, indent=2, default=str), file=sys.stderr)
            ctx.simulated.append({"action": "add_attachment", **action_desc})
            return _jtext(
                {
                    "dry_run": True,
                    "would_attach_to": bug_id,
                    "file_name": file_name,
                    "size_bytes": size,
                    "note": "No request sent. Re-run without --dry-run to apply.",
                }
            )

        if ctx.confirm and not await _confirm_prompt(
            f"attach {file_name} ({size} bytes) to bug {bug_id}", action_desc
        ):
            return _jtext(
                {
                    "skipped": True,
                    "bug_id": bug_id,
                    "reason": "User declined at --confirm prompt.",
                }
            )

        # Read + encode here, server-side — keeps the agent's tool call tiny
        # (just a path string) instead of forcing it to stream base64 tokens.
        with open(file_path, "rb") as fp:
            data = base64.b64encode(fp.read()).decode("ascii")

        body = {
            "ids": [bug_id],
            "data": data,
            "file_name": file_name,
            "summary": summary,
            "content_type": content_type,
            "is_patch": is_patch,
        }

        try:
            result = ctx.client.request(
                f"bug/{bug_id}/attachment", method="POST", json=body
            )
        except bugsy.BugsyException as e:
            return _handle_bugsy_error(e)

        response = {"attached_to": bug_id, "result": result}

        # The attachment endpoint's inline comment field doesn't honour
        # is_markdown — post the comment separately so markdown renders.
        comment = args.get("comment")
        if comment:
            footer = (
                "*This is the analysis tool's suggested fix. Feel welcome "
                "to adopt it as a starting point and evolve it as needed to "
                "meet our coding standards.*"
            )
            comment = comment.rstrip() + "\n\n" + footer
            try:
                cres = ctx.client.request(
                    f"bug/{bug_id}/comment",
                    method="POST",
                    json={"comment": comment, "is_markdown": True},
                )
                response["comment_id"] = cres.get("id")
            except bugsy.BugsyException as e:
                # Attachment already succeeded; surface the comment failure
                # without clobbering that fact.
                response["comment_error"] = str(e)

        return _jtext(response)

    @tool(
        "create_bug",
        "File a new bug. Requires the proxy key to have allow_create=true. "
        "The description becomes comment 0 and is rendered as Markdown. "
        "Pass any additional Bugzilla POST /bug fields (severity, priority, "
        "keywords, whiteboard, blocks, depends_on, cc, groups, op_sys, "
        "platform, assigned_to, see_also, ...) via 'extra'. "
        "Honours --dry-run and --confirm.",
        {
            "type": "object",
            "properties": {
                "product": {"type": "string"},
                "component": {"type": "string"},
                "summary": {"type": "string"},
                "version": {"type": "string"},
                "description": {
                    "type": "string",
                    "description": "Comment 0. Markdown is enabled.",
                },
                "extra": {
                    "type": "object",
                    "description": (
                        "Optional additional fields accepted by Bugzilla's "
                        "POST /bug endpoint. Merged into the request body."
                    ),
                    "additionalProperties": True,
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why you are filing this bug (for audit log).",
                },
            },
            "required": [
                "product",
                "component",
                "summary",
                "version",
                "description",
                "reasoning",
            ],
        },
    )
    async def create_bug(args):
        reasoning = args["reasoning"]
        body = {
            "product": args["product"],
            "component": args["component"],
            "summary": args["summary"],
            "version": args["version"],
            "description": args["description"],
            "is_markdown": True,
        }
        extra = args.get("extra") or {}
        # Explicit top-level args win over anything smuggled in via extra.
        for k, v in extra.items():
            body.setdefault(k, v)

        action_desc = {"reasoning": reasoning, "body": body}

        if ctx.dry_run:
            print("\n[DRY-RUN] create_bug", file=sys.stderr)
            print(json.dumps(action_desc, indent=2, default=str), file=sys.stderr)
            ctx.simulated.append({"action": "create_bug", **action_desc})
            return _jtext(
                {
                    "dry_run": True,
                    "would_create": body["summary"],
                    "body": body,
                    "note": "No request sent. Re-run without --dry-run to apply.",
                }
            )

        if ctx.confirm and not await _confirm_prompt("create bug", action_desc):
            return _jtext(
                {
                    "skipped": True,
                    "reason": "User declined at --confirm prompt.",
                }
            )

        try:
            result = ctx.client.request("bug", method="POST", json=body)
        except bugsy.BugsyException as e:
            return _handle_bugsy_error(e)
        return _jtext({"created": result.get("id"), "result": result})

    return create_sdk_mcp_server(
        name="bugzilla",
        version="0.1.0",
        tools=[
            search_bugs,
            get_bugs,
            get_bug_comments,
            get_bug_attachments,
            download_attachment,
            update_bug,
            add_comment,
            add_attachment,
            create_bug,
        ],
    )
