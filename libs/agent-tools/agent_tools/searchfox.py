"""Read-only Searchfox code-search tools for Firefox (mozilla-central).

Framework-neutral: each tool is a ``@tool``-decorated handler whose first
parameter is a :class:`SearchfoxContext`. Backed by the standalone ``searchfox``
client (the ``searchfox`` optional extra) — this module deliberately does NOT
import the ``bugbug`` package, so it stays light enough to ship in an agent
image. Network/request failures surface as a structured
:class:`~agent_tools.registry.ToolError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import Field
from searchfox import (
    AsyncSearchfoxClient,
    SearchfoxNetworkError,
    SearchfoxRequestError,
)

from agent_tools.registry import ToolError, tool, tools_in


@dataclass
class SearchfoxContext:
    """Holds a shared async Searchfox client (one connection pool per run)."""

    client: AsyncSearchfoxClient


def _sf_error(e: Exception, what: str) -> ToolError:
    """Render a searchfox client failure as a structured, machine-parseable error."""
    return ToolError(
        f"{what}: {e}",
        payload={"error": "searchfox_error", "what": what, "message": str(e)},
    )


def _fmt_hits(results) -> str:
    """Format ``(path, line, content)`` search hits into one line each."""
    if not results:
        return "No results found."
    return "\n".join(f"{path}:{line}: {content}" for path, line, content in results)


@tool
async def search_identifier(
    ctx: SearchfoxContext,
    identifier: Annotated[
        str,
        Field(description="Exact identifier/symbol to find, e.g. 'showWeatherOptIn'."),
    ],
    path_filter: Annotated[
        str | None,
        Field(description="Optional path prefix, e.g. 'browser/components/newtab'."),
    ] = None,
    limit: Annotated[int, Field(description="Max results.")] = 50,
) -> str:
    """Search for an exact identifier across mozilla-central.

    Best first step for localizing where a symbol (pref name, function, variable,
    DOM attribute, CSS class) is declared and used. Returns 'path:line: content'.
    """
    try:
        results = await ctx.client.search(id=identifier, path=path_filter, limit=limit)
    except (SearchfoxNetworkError, SearchfoxRequestError) as e:
        raise _sf_error(e, "identifier search failed") from e
    return _fmt_hits(results)


@tool
async def search_text(
    ctx: SearchfoxContext,
    query: Annotated[
        str, Field(description="Text or regular expression to search for.")
    ],
    path_filter: Annotated[
        str | None, Field(description="Optional path prefix, e.g. 'browser/components'.")
    ] = None,
    regexp: Annotated[
        bool, Field(description="Treat the query as a regular expression.")
    ] = False,
    case_sensitive: Annotated[bool, Field(description="Case-sensitive matching.")] = False,
    limit: Annotated[int, Field(description="Max results.")] = 50,
) -> str:
    """Full-text / regex search across mozilla-central.

    Use for strings the user/bug quotes (UI labels, error text, CSS selectors).
    Returns 'path:line: content' entries.
    """
    try:
        results = await ctx.client.search(
            query=query,
            path=path_filter,
            regexp=regexp,
            case=case_sensitive,
            limit=limit,
        )
    except (SearchfoxNetworkError, SearchfoxRequestError) as e:
        raise _sf_error(e, "text search failed") from e
    return _fmt_hits(results)


@tool
async def find_definition(
    ctx: SearchfoxContext,
    name: Annotated[
        str,
        Field(
            description=(
                "Symbol name (function/method/class), partial (e.g. 'WeatherFeed') "
                "or fully-qualified (e.g. 'WeatherFeed.checkOptInRegion')."
            )
        ),
    ],
    path_filter: Annotated[
        str | None, Field(description="Optional path prefix to disambiguate.")
    ] = None,
) -> str:
    """Return the source of a symbol's definition."""
    try:
        return await ctx.client.get_definition(name, path_filter)
    except (SearchfoxNetworkError, SearchfoxRequestError) as e:
        raise _sf_error(e, "definition lookup failed") from e


@tool
async def get_function_at_line(
    ctx: SearchfoxContext,
    file_path: Annotated[
        str,
        Field(
            description=(
                "Repo-relative path, e.g. "
                "'browser/components/newtab/lib/WeatherFeed.sys.mjs'."
            )
        ),
    ],
    line: Annotated[int, Field(description="1-based line number inside the function.")],
) -> str:
    """Return the source of the innermost function enclosing a given line.

    Useful when you have a line number (e.g. from a stack trace or blame) and want
    the full enclosing function without knowing its name.
    """
    try:
        return await ctx.client.get_function_at_line(file_path, line)
    except (SearchfoxNetworkError, SearchfoxRequestError) as e:
        raise _sf_error(e, "function lookup failed") from e


@tool
async def get_blame(
    ctx: SearchfoxContext,
    file_path: Annotated[str, Field(description="Repo-relative path.")],
    lines: Annotated[list[int], Field(description="1-based line numbers to look up.")],
) -> str:
    """Return the changeset that last modified each given line.

    Format: 'LINE: HASH (DATE) MESSAGE'. Use this to find the change (and thus the
    bug) that introduced a line — e.g. to confirm a suspected regressor.
    """
    try:
        results = await ctx.client.get_blame_for_lines(file_path, lines)
    except (SearchfoxNetworkError, SearchfoxRequestError) as e:
        raise _sf_error(e, "blame fetch failed") from e
    if not results:
        return "No blame information found."
    return "\n".join(
        f"{line}: {hash_} ({date}) {message}"
        for line, hash_, message, date in results
    )


@tool
async def get_file(
    ctx: SearchfoxContext,
    file_path: Annotated[str, Field(description="Repo-relative path.")],
    revision: Annotated[
        str | None,
        Field(description="Optional hg revision/node; omit for the current tip."),
    ] = None,
) -> str:
    """Return the full content of a file (at HEAD, or at a specific revision)."""
    try:
        if revision:
            return await ctx.client.get_file_at_revision(file_path, revision)
        return await ctx.client.get_file(file_path)
    except (SearchfoxNetworkError, SearchfoxRequestError) as e:
        raise _sf_error(e, "file fetch failed") from e


TOOLS = tools_in(__name__)
