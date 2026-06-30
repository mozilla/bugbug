"""Read-only Mozilla VCS tools backed by hg.mozilla.org (HGMO).

Framework-neutral HTTP tools for inspecting changesets on Mozilla's Mercurial
server. The primary use is reading the **diff** of a known regressor changeset to
pinpoint what changed — something Searchfox blame alone does not provide. HGMO is
public, so this holds no credentials. It issues plain HTTP GETs (no local clone),
so it works regardless of how shallow the agent's checkout is. Failures surface as
a structured :class:`~agent_tools.registry.ToolError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import httpx
from pydantic import Field

from agent_tools.registry import ToolError, tool, tools_in


@dataclass
class MozillaVcsContext:
    """Configuration for HGMO access (no credentials — HGMO is public)."""

    base_url: str = "https://hg.mozilla.org"
    default_repo: str = "mozilla-central"
    user_agent: str = "bugbug-frontend-triage"
    timeout: float = 30.0
    # Cap diff size so a huge changeset can't blow up the agent's context.
    max_diff_bytes: int = 60_000


async def _get(ctx: MozillaVcsContext, path: str, *, as_json: bool):
    url = f"{ctx.base_url}/{path}"
    headers = {"User-Agent": ctx.user_agent}
    try:
        async with httpx.AsyncClient(
            timeout=ctx.timeout, follow_redirects=True
        ) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        raise ToolError(
            f"request failed: {e}",
            payload={"error": "http_error", "url": url, "message": str(e)},
        ) from e
    if resp.status_code == 404:
        raise ToolError(
            "changeset or path not found",
            payload={"error": "not_found", "url": url},
        )
    if resp.status_code >= 400:
        raise ToolError(
            f"HTTP {resp.status_code}",
            payload={"error": "http_status", "status": resp.status_code, "url": url},
        )
    return resp.json() if as_json else resp.text


@tool
async def get_commit_info(
    ctx: MozillaVcsContext,
    node: Annotated[
        str, Field(description="Changeset hash (hg node), e.g. '6b8a3f804789'.")
    ],
    repo: Annotated[
        str | None,
        Field(
            description=(
                "Repo name; defaults to mozilla-central. Other common values: "
                "'autoland', 'mozilla-unified'."
            )
        ),
    ] = None,
) -> dict:
    """Return metadata for a changeset: author, date, description, parents, files.

    Use this to understand a known regressor changeset (e.g. the landing of the bug
    referenced in a bug's 'regressed_by' field) before reading its diff.
    """
    repo = repo or ctx.default_repo
    data = await _get(ctx, f"{repo}/json-rev/{node}", as_json=True)
    return {
        "node": data.get("node"),
        "repo": repo,
        "author": data.get("user"),
        "date": data.get("date"),
        "pushdate": data.get("pushdate"),
        "desc": data.get("desc"),
        "parents": data.get("parents"),
        "files": data.get("files"),
    }


@tool
async def get_commit_diff(
    ctx: MozillaVcsContext,
    node: Annotated[str, Field(description="Changeset hash (hg node).")],
    repo: Annotated[
        str | None,
        Field(description="Repo name; defaults to mozilla-central."),
    ] = None,
) -> dict:
    """Return the unified diff (hg export) of a changeset.

    The key tool for regression triage: read exactly what a regressor changeset
    changed so you can localize the fix precisely. Large diffs are truncated (see
    the 'truncated' flag); narrow with a follow-up if needed.
    """
    repo = repo or ctx.default_repo
    text = await _get(ctx, f"{repo}/raw-rev/{node}", as_json=False)
    raw = text.encode("utf-8")
    truncated = len(raw) > ctx.max_diff_bytes
    if truncated:
        text = raw[: ctx.max_diff_bytes].decode("utf-8", "ignore")
    return {"node": node, "repo": repo, "truncated": truncated, "diff": text}


@tool
async def file_history(
    ctx: MozillaVcsContext,
    path: Annotated[str, Field(description="Repo-relative file path.")],
    repo: Annotated[
        str | None,
        Field(description="Repo name; defaults to mozilla-central."),
    ] = None,
    limit: Annotated[int, Field(description="Max changesets to return.")] = 20,
) -> dict:
    """Return recent changesets that modified a file (newest first).

    Useful when a bug is a regression but its 'regressed_by' field is empty: scan
    the recent history of the suspected file to find a likely introducing change.
    """
    repo = repo or ctx.default_repo
    data = await _get(ctx, f"{repo}/json-log/tip/{path}", as_json=True)
    entries = data.get("entries", []) if isinstance(data, dict) else []
    changesets = [
        {
            "node": e.get("node"),
            "date": e.get("date"),
            "author": e.get("user"),
            "desc": (e.get("desc") or "").splitlines()[0] if e.get("desc") else "",
        }
        for e in entries[:limit]
    ]
    return {
        "repo": repo,
        "path": path,
        "count": len(changesets),
        "changesets": changesets,
    }


TOOLS = tools_in(__name__)
