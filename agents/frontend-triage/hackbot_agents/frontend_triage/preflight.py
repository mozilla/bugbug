"""Stop a run before it triages a bug whose fix is already written.

``rules/scoping.md`` has always said to stop on these, but a ruleset is advisory:
on bug 2066504 the agent read the attachment, named the revision, and investigated
anyway. This is the same rule somewhere the model cannot outrank it.

The gate is one-directional -- all it can do is stop a run -- so everything it
cannot read falls through to a triage. A needless triage costs a dollar; a wrongly
skipped bug costs a triage nobody notices is missing.
"""

from __future__ import annotations

import json
import sys

from claude_agent_sdk import McpServerConfig
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# `id` is required: `get_bugs` diffs requested against returned ids to report
# inaccessible ones, and raises KeyError without it. The attachment fields are
# dotted so Bugzilla leaves `data` out -- a bare `attachments` returns every
# attachment's base64 body.
BUG_FIELDS = (
    "id,product,component,"
    "attachments.id,attachments.content_type,attachments.is_obsolete"
)

# Matched by name rather than by `is_patch`, which is 0 on one of these: the
# attachment is a URL, and the diff itself lives on Phabricator.
PHABRICATOR_CONTENT_TYPE = "text/x-phabricator-request"


def attached_fix(bug: dict) -> str | None:
    """The revision already attached to this bug, or None to triage it."""
    for attachment in bug.get("attachments") or ():
        # Truthiness rather than `== 1`: Bugzilla sends these as ints.
        if not isinstance(attachment, dict) or attachment.get("is_obsolete"):
            continue
        content_type = (attachment.get("content_type") or "").strip().lower()
        if content_type == PHABRICATOR_CONTENT_TYPE:
            return (
                f"a Phabricator revision is attached "
                f"(attachment {attachment.get('id')})"
            )
    return None


async def fetch_bug(bugzilla_mcp_server: McpServerConfig, bug: int) -> dict:
    """The bug's :data:`BUG_FIELDS`, read through the Bugzilla broker.

    Via the broker because the agent container holds no Bugzilla credentials.
    Never raises: ``{}`` on a missing url, a broker failure, or a bug this key
    cannot read.
    """
    url = (
        bugzilla_mcp_server.get("url")
        if isinstance(bugzilla_mcp_server, dict)
        else None
    )
    if not url:
        return {}

    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(
                    "get_bugs",
                    {"ids": [bug], "include_fields": BUG_FIELDS},
                )
                if res.isError:
                    raise RuntimeError(
                        "".join(getattr(c, "text", "") for c in res.content)
                    )
                bugs = json.loads(res.content[0].text).get("bugs") or []
                return bugs[0] if bugs else {}
    except Exception as e:  # - see docstring; every failure fails open
        print(
            f"[frontend_triage] pre-flight bug lookup failed "
            f"({type(e).__name__}: {e}); triaging without it",
            file=sys.stderr,
        )
        return {}
