"""Read-only Bugzilla tools backed by bugsy.

Framework-neutral: each tool is a ``@tool``-decorated handler whose first
parameter is a :class:`BugzillaContext`. Handlers return plain data and surface
proxy-level restrictions (code 101: endpoint not exposed, code 102: access
denied) as a structured :class:`~agent_tools.registry.ToolError`.
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Annotated, Any

import bugsy
from pydantic import Field

from agent_tools.registry import ToolError, tool, tools_in

# What Claude can actually interpret once an attachment reaches it — as an image
# block via the built-in Read tool, or as text. Deliberately narrower than what
# Bugzilla accepts: a screencast costs a full download and tens of thousands of
# tokens and still comes back undecodable, which is what
# https://github.com/mozilla/bugbug/issues/6701 was filed about. Anything absent
# is refused, so a new video or archive format needs no matching reject list.
ALLOWED_ATTACHMENT_TYPES = frozenset(
    {
        # The four image formats the Anthropic API accepts as image blocks.
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        # Not rendered as an image, but readable as markup.
        "image/svg+xml",
        "application/pdf",
        # Textual application/* types Bugzilla serves for logs, testcases, configs.
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-javascript",
    }
)

# Every text/* subtype is allowed on top of the set above — text/plain, text/html,
# text/css, text/csv, text/x-log and text/x-phabricator-request are all just text,
# and enumerating them would only mean missing one.
_ALLOWED_TYPE_PREFIX = "text/"

# Bugzilla records the type the uploader chose, so a plain log routinely arrives
# as application/octet-stream, and so does a screencast saved without a type.
# Resolve those from the file name instead.
_OCTET_STREAM = "application/octet-stream"

# Python's built-in table, with the system mime database deliberately left out
# (`filenames=()`). Reading /etc/mime.types would make the verdict depend on the
# host: .log resolves to text/plain on a macOS dev box via /etc/apache2/mime.types
# and to nothing in the CI and agent containers, so an update.log would be
# readable on a laptop and refused in production. The built-in table already
# covers .md, .json, .csv, .html, .png and, on the reject side, .mp4, .webm,
# .mov, .zip and .tar.
_MIMETYPES = mimetypes.MimeTypes(filenames=())

# What the built-in table does not know, and Bugzilla reporters attach anyway.
_EXTENSION_TYPES = {
    ".log": "text/plain",
    ".diff": "text/plain",
    ".patch": "text/plain",
}

# `claude_sdk._make_tool` can only emit {"type": "text"}, so a base64 image in a
# tool result is never an image block, just a very long string.
INLINEABLE_ATTACHMENT_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-javascript",
        # Markup, so the text is the content.
        "image/svg+xml",
    }
)

# Bugzilla accepts attachments up to 10MB, and base64 inflates by 4/3, so an
# unbounded inline is ~13MB of text for one file. 256KiB decoded is ~350KB of
# base64, on the order of 85k tokens: enough for an update.log or a testcase,
# and past that download_attachment plus Grep beats reading the whole thing.
MAX_INLINE_BYTES = 256 * 1024

MAX_INLINE_ATTACHMENTS = 10

# The allowlist in the form the agent reads back in an error payload.
_ALLOWED_SUMMARY = (
    "text/*, images (png/jpeg/gif/webp/svg), PDF, and JSON/XML/JavaScript"
)


def _normalize_type(content_type: str | None) -> str:
    """Lowercase a content type and drop its parameters.

    Bugzilla passes through whatever the uploader sent, so ``TEXT/PLAIN`` and
    ``text/plain; charset=UTF-8`` both turn up and both mean text/plain.
    """
    return (content_type or "").split(";", 1)[0].strip().lower()


def _effective_content_type(att: dict[str, Any]) -> str:
    """Resolve what an attachment is, rather than only what it claims to be.

    ``is_patch`` wins outright because a Bugzilla patch is text by definition —
    ``hackbot_runtime.actions.bugzilla.add_attachment`` makes the same assumption
    in the write direction. Returns "" when nothing identifies the attachment,
    which the caller treats as not readable.
    """
    if att.get("is_patch"):
        return "text/plain"
    content_type = _normalize_type(att.get("content_type"))
    if content_type and content_type != _OCTET_STREAM:
        return content_type
    file_name = att.get("file_name") or ""
    suffix = PurePosixPath(file_name).suffix.lower()
    guessed = _EXTENSION_TYPES.get(suffix) or _MIMETYPES.guess_type(file_name)[0]
    return _normalize_type(guessed)


def attachment_type_allowed(att: dict[str, Any]) -> tuple[bool, str]:
    """Return whether an attachment is readable, and the type that decided it.

    The type comes back too so a caller can say *what* it refused instead of only
    that it refused something.
    """
    effective = _effective_content_type(att)
    allowed = (
        effective.startswith(_ALLOWED_TYPE_PREFIX)
        or effective in ALLOWED_ATTACHMENT_TYPES
    )
    return allowed, effective


def _inlineable(effective_content_type: str) -> bool:
    """Whether this type is worth base64-ing into a tool result rather than a file."""
    return (
        effective_content_type.startswith(_ALLOWED_TYPE_PREFIX)
        or effective_content_type in INLINEABLE_ATTACHMENT_TYPES
    )


def _type_not_allowed_error(
    attachment_id: int, att: dict[str, Any], effective: str
) -> ToolError:
    """Build the refusal an agent sees for an attachment it cannot read.

    ``claude_sdk`` renders a ToolError payload as the tool result itself, so the
    hint is the only place to keep the agent from burning turns retrying, and to
    tell it to disclose that it never saw the attachment.
    """
    return ToolError(
        f"attachment {attachment_id} is "
        f"{effective or 'of an unrecognized type'}, which Claude cannot read",
        payload={
            "error": "attachment_type_not_allowed",
            "attachment_id": attachment_id,
            "file_name": att.get("file_name"),
            "content_type": att.get("content_type"),
            "effective_content_type": effective or None,
            "allowed": _ALLOWED_SUMMARY,
            "hint": (
                "Video, audio, and archive attachments are refused on purpose — "
                "Claude cannot interpret them and downloading one is expensive. "
                "Do not retry. Work from the bug's text and say in your comment "
                "that you did not view this attachment."
            ),
        },
    )


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


def _request(ctx: BugzillaContext, path: str, params: dict[str, Any] | None = None):
    """Issue a Bugzilla request, normalizing every failure into a ToolError.

    bugsy only raises ``BugsyException`` for Bugzilla-level errors; a bad proxy
    URL, an auth redirect, or an empty body instead surfaces as a raw
    ``JSONDecodeError``/connection error. Catching those here turns an opaque
    "Expecting value: line 1 column 1" into an actionable message.
    """
    try:
        return ctx.client.request(path, params=params or {})
    except bugsy.BugsyException as e:
        raise _bugsy_error(e) from e
    except Exception as e:
        raise ToolError(
            f"Bugzilla request to '{path}' failed: {type(e).__name__}: {e}",
            payload={
                "error": "bugzilla_request_failed",
                "path": path,
                "message": str(e),
            },
        ) from e


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
    result = _request(ctx, "bug", params)
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
    result = _request(ctx, "bug", {"id": id_csv, "include_fields": include})
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
        except Exception as e:
            payload["comments_error"] = {"message": f"{type(e).__name__}: {e}"}

    return payload


@tool
async def get_bug_comments(
    ctx: BugzillaContext,
    bug_id: Annotated[int, Field(description="Bug ID.")],
) -> dict:
    """Fetch all comments for a single bug."""
    result = _request(ctx, f"bug/{bug_id}/comment")
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
                "If true, inline base64 content for the textual attachments "
                "only (text/*, JSON, XML, JavaScript, SVG) under 256KiB. Images "
                "and PDFs are never inlined; use download_attachment for those."
            )
        ),
    ] = False,
) -> dict:
    """Fetch attachments for a bug.

    By default returns metadata only (cheap, safe for large binaries). Set
    include_data=true to also inline the content, base64-encoded in each
    attachment's 'data' field, for the textual types only: text/*, JSON, XML,
    JavaScript, and SVG, under 256KiB, for the first 10 that qualify.

    Anything else keeps its metadata and gains a 'data_omitted' note saying why.
    Images and PDFs are among them on purpose: a tool result is text, so a
    base64 image is a long string rather than something Claude can see. Use
    download_attachment and read the file for those. An attachment the proxy
    refuses gets a 'data_error' and does not take the rest of the response with
    it.
    """
    result = _request(ctx, f"bug/{bug_id}/attachment", {"exclude_fields": "data"})
    atts = result.get("bugs", {}).get(str(bug_id), [])
    payload = {"bug_id": bug_id, "count": len(atts), "attachments": atts}
    if not include_data:
        return payload

    # The list endpoint is all-or-nothing on `data`, so asking it for content
    # would pull a screencast over the wire before we could drop it. Fetching the
    # inlineable attachments one at a time never moves the bytes this is here to
    # avoid, at the cost of one request each.
    inlined = 0
    omitted = 0
    errors = 0
    for att in atts:
        allowed, effective = attachment_type_allowed(att)
        size = att.get("size")
        if not allowed:
            note = f"{effective or 'unrecognized type'} is not readable by Claude"
        elif not _inlineable(effective):
            note = (
                f"{effective} is binary, and a tool result is text, so inlining it "
                "would cost the whole file in tokens without producing an image. "
                "Use download_attachment and read the file."
            )
        elif isinstance(size, int) and size > MAX_INLINE_BYTES:
            note = (
                f"{size} bytes is over the {MAX_INLINE_BYTES}-byte inline limit. "
                "Use download_attachment, then Grep the file for what you need."
            )
        elif inlined >= MAX_INLINE_ATTACHMENTS:
            note = (
                f"only the first {MAX_INLINE_ATTACHMENTS} attachments are inlined "
                "per call. Use download_attachment for this one."
            )
        else:
            try:
                one = _request(ctx, f"bug/attachment/{att['id']}")
            except ToolError as e:
                att["data_error"] = e.payload or {"message": str(e)}
                errors += 1
                continue
            fetched = one.get("attachments", {}).get(str(att["id"]))
            if fetched is None:
                att["data_error"] = {"error": "attachment_not_returned"}
                errors += 1
                continue
            att["data"] = fetched.get("data")
            inlined += 1
            continue
        att["data_omitted"] = note
        omitted += 1

    payload["inlined_count"] = inlined
    payload["omitted_count"] = omitted
    payload["error_count"] = errors
    return payload


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

    Only types Claude can read are downloaded: text/*, png/jpeg/gif/webp/svg
    images, PDF, and JSON/XML/JavaScript. Video, audio, and archives fail with
    'attachment_type_not_allowed' — a permanent answer, not a retryable one.
    """
    # Metadata first, so a refused attachment's bytes never leave Bugzilla. One
    # extra round trip on the happy path buys that.
    meta = _request(ctx, f"bug/attachment/{attachment_id}", {"exclude_fields": "data"})

    att = meta.get("attachments", {}).get(str(attachment_id))
    if att is None:
        raise ToolError(
            f"attachment {attachment_id} not found",
            payload={"error": "attachment_not_found", "attachment_id": attachment_id},
        )

    allowed, effective = attachment_type_allowed(att)
    if not allowed:
        raise _type_not_allowed_error(attachment_id, att, effective)

    full = _request(ctx, f"bug/attachment/{attachment_id}")
    data = full.get("attachments", {}).get(str(attachment_id), {}).get("data")
    if data is None:
        raise ToolError(
            f"attachment {attachment_id} came back without data",
            payload={"error": "attachment_no_data", "attachment_id": attachment_id},
        )

    raw = base64.b64decode(data)
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
