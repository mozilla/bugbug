"""Decisions made in Python before any model turn: write mode and profile URLs.

Both are facts about the bug, not judgements, so they are read here rather than
left to the model -- the same reason frontend-triage stops on an attached patch in
``preflight.py`` rather than in a ruleset. Finding profile URLs in code also means
a bug without one pays nothing extra: the skill's profile step is only switched on
when this module found something to analyse.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Literal

from claude_agent_sdk import McpServerConfig
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# `id` is required: `get_bugs` diffs requested against returned ids.
BUG_FIELDS = "id,product,component,cf_performance_impact,keywords,url"

WriteMode = Literal["full", "comment_only"]

# Values that mean nobody has rated the bug. Anything else is a person's rating,
# which this agent may comment on but never overwrites.
_UNRATED = {"?", "---", "", None}

# A shared short link, or a profiler.firefox.com URL that names a profile. The
# bare site (https://profiler.firefox.com/) is where you capture one, not one.
_PROFILE_URL = re.compile(
    r"https://(?:share\.firefox\.dev/[A-Za-z0-9]+"
    r"|profiler\.firefox\.com/(?:public|from-url)/[^\s)\]>\"']+)"
)


def write_mode(bug: dict) -> WriteMode:
    """``full`` when the bug is unrated, ``comment_only`` when a person rated it."""
    return "full" if bug.get("cf_performance_impact") in _UNRATED else "comment_only"


# An exported profile attached to the bug. Matched on the name the profiler
# gives its exports, not the gzip type: about:memory reports are .json.gz too.
_PROFILE_ATTACHMENT = re.compile(r"profile.*\.json(\.gz)?$", re.IGNORECASE)

# profiler-cli fetches the attachment itself, without credentials, so a private
# attachment is skipped rather than handed over as a URL that would 401.
ATTACHMENT_URL = "https://bugzilla.mozilla.org/attachment.cgi?id={}"


def profile_urls(
    bug: dict, comments: list[str], attachments: list[dict] = ()
) -> list[str]:
    """Profile links from the URL field, the comments, then attached profiles."""
    found: list[str] = []
    for text in [bug.get("url") or "", *comments]:
        for url in _PROFILE_URL.findall(text):
            url = url.rstrip(".,;:")
            if url not in found:
                found.append(url)
    for att in attachments:
        if (
            not att.get("is_obsolete")
            and not att.get("is_private")
            and _PROFILE_ATTACHMENT.search(att.get("file_name") or "")
        ):
            url = ATTACHMENT_URL.format(att["id"])
            if url not in found:
                found.append(url)
    return found


async def _call(server: McpServerConfig, tool: str, args: dict) -> dict:
    url = server.get("url") if isinstance(server, dict) else None
    if not url:
        return {}
    async with (
        streamablehttp_client(url) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        res = await session.call_tool(tool, args)
        if res.isError:
            raise RuntimeError("".join(getattr(c, "text", "") for c in res.content))
        return json.loads(res.content[0].text)


async def fetch_bug(server: McpServerConfig, bug_id: int) -> dict:
    """The bug's :data:`BUG_FIELDS` through the broker; ``{}`` on any failure."""
    try:
        bugs = (
            await _call(
                server, "get_bugs", {"ids": [bug_id], "include_fields": BUG_FIELDS}
            )
        ).get("bugs") or []
        return bugs[0] if bugs else {}
    except Exception as e:  # - fail open, see module docstring
        print(
            f"[perf_triage] bug lookup failed ({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return {}


async def fetch_comments(server: McpServerConfig, bug_id: int) -> list[str]:
    """Comment texts through the broker; ``[]`` on any failure."""
    try:
        data = await _call(server, "get_bug_comments", {"bug_id": bug_id})
        comments = data.get("comments") or []
        return [c.get("text", "") for c in comments if isinstance(c, dict)]
    except Exception as e:
        print(
            f"[perf_triage] comment lookup failed ({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return []


async def fetch_attachments(server: McpServerConfig, bug_id: int) -> list[dict]:
    """Attachment metadata through the broker; ``[]`` on any failure."""
    try:
        data = await _call(server, "get_bug_attachments", {"bug_id": bug_id})
        return [a for a in data.get("attachments") or [] if isinstance(a, dict)]
    except Exception as e:
        print(
            f"[perf_triage] attachment lookup failed ({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return []
