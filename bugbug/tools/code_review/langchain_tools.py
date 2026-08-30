# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""LangGraph tools for code review agent."""

from dataclasses import dataclass
from functools import cache
from logging import getLogger
from typing import Annotated, Literal, Optional

import httpx
import tenacity
from langchain.tools import tool
from langgraph.runtime import get_runtime
from searchfox import AsyncSearchfoxClient, SearchfoxNetworkError, SearchfoxRequestError

from bugbug.tools.code_review.data_types import Skill, SkillLoadError
from bugbug.tools.core.platforms.base import Patch
from bugbug.tools.core.platforms.patch_apply import get_file_after_stack
from bugbug.tools.core.validators import StripEnumQuotes

logger = getLogger(__name__)

_retry = tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, min=1, max=4),
    retry=tenacity.retry_if_exception_type(SearchfoxNetworkError),
    reraise=True,
)


def _tool_error(message: str, *, fatal: bool = False) -> str:
    prefix = "Fatal" if fatal else "Warning"
    return f"{prefix}: {message}"


LangStr = Annotated[
    Literal[
        "cpp", "c", "js", "webidl", "java", "kotlin", "rust", "python", "html", "css"
    ],
    StripEnumQuotes,
]
Tests = Optional[Annotated[Literal["only", "exclude"], StripEnumQuotes]]


@dataclass
class CodeReviewContext:
    patch: Patch


@cache
def _get_client() -> AsyncSearchfoxClient:
    return AsyncSearchfoxClient()


async def _fetch_file(
    path: str,
    revision: Optional[str],
    client: AsyncSearchfoxClient,
    patch: Patch,
) -> str:
    try:
        return await patch.get_old_file(path)
    except (FileNotFoundError, httpx.HTTPStatusError):
        pass
    if revision:
        return await _retry(client.get_file_at_revision)(path, revision)
    return await _retry(client.get_file)(path)


@tool
async def expand_context(
    file_path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> str:
    """Retrieve the content of a file, optionally restricted to a line range.

    Omit start_line and end_line to get the full file. When specifying a range,
    be careful to not fill your context window with too much data — request the
    minimum necessary, but do not split a continuous range into multiple requests.

    Args:
        file_path: Repository-relative path, e.g. 'dom/media/webaudio/AudioNode.h'.
        start_line: Starting line number (1-based). Omit to start from the beginning.
        end_line: Ending line number (inclusive). Omit to read to the end of the file.

    Returns:
        The file content, with line numbers prefixed.
    """
    runtime = get_runtime(CodeReviewContext)
    patch = runtime.context.patch

    warning = None
    try:
        patch_stack = patch.patch_stack
    except ValueError as e:
        warning = f"Could not retrieve the full patch stack ({e}). File content reflects only this patch; please flag this in your review."
        patch_stack = [patch.patch_set]

    revision = await patch.get_base_revision()
    client = _get_client()

    async def fetch(path: str) -> str:
        return await _fetch_file(path, revision, client, patch)

    try:
        file_content = await get_file_after_stack(patch_stack, file_path, fetch)
    except FileNotFoundError:
        return f"Warning: {file_path} was removed by the patch stack."
    except Exception as e:
        return f"Warning: could not retrieve {file_path}: {e}."

    lines = file_content.splitlines()
    start = max(1, start_line) - 1 if start_line is not None else 0
    end = min(len(lines), end_line) if end_line is not None else len(lines)

    line_number_width = len(str(end))
    content = "\n".join(
        f"{i + 1:>{line_number_width}}| {lines[i]}" for i in range(start, end)
    )
    if warning:
        return f"Warning: {warning}\n\n{content}"
    return content


def create_load_skill_tool(skills: list[Skill]):
    skills_by_name = {skill.name: skill for skill in skills}
    available_names = ", ".join(skills_by_name)

    catalog_lines = "\n".join(
        f"- **{skill.name}**: {skill.description}" for skill in skills
    )
    description = (
        "Load the contents of a named skill to guide the review. Use this when "
        "the patch touches an area covered by one of the skills below; otherwise, "
        "do not call it.\n\n"
        "Available skills:\n"
        f"{catalog_lines}\n\n"
        "Args:\n"
        "    name: The name of the skill to load (must match one of the names above).\n\n"
        "Returns:\n"
        "    The skill content as Markdown."
    )

    @tool(description=description)
    async def load_skill(name: str) -> str:
        skill = skills_by_name.get(name)
        if skill is None:
            return f"Unknown skill '{name}'. Available: {available_names}."

        try:
            return await skill.load()
        except SkillLoadError:
            logger.exception("Failed to load skill '%s'", name)
            return f"Failed to load skill '{name}'. Please proceed without it."

    return load_skill


@tool
async def search_text(
    query: str,
    path_filter: Optional[str] = None,
    langs: Optional[list[LangStr]] = None,
    tests: Tests = None,
    regexp: bool = False,
    case_sensitive: bool = False,
    limit: int = 50,
    context_lines: Optional[int] = None,
) -> str:
    """Search for text or patterns across the codebase.

    Args:
        query: Text or regular expression to search for.
        path_filter: Optional path prefix, e.g. 'dom/media'.
        langs: Optional language filter. Multiple values are OR-ed.
        tests: 'only' to restrict to test files, 'exclude' to omit them.
        regexp: Treat query as a regular expression.
        case_sensitive: Enable case-sensitive matching.
        limit: Maximum number of results (default 50).
        context_lines: Surrounding lines to include per match.

    Returns:
        Matching lines as 'path:line: content' entries.
    """
    try:
        results = await _get_client().search(
            query=query,
            path=path_filter,
            langs=langs,
            tests=tests,
            regexp=regexp,
            case=case_sensitive,
            limit=limit,
            context=context_lines,
        )
        if not results:
            return "No results found."
        return "\n".join(f"{path}:{line}: {content}" for path, line, content in results)
    except SearchfoxNetworkError as e:
        return _tool_error(f"search failed: {e}")
    except SearchfoxRequestError as e:
        logger.warning(
            "Bad request searching for %r (path=%r, langs=%r): %s",
            query,
            path_filter,
            langs,
            e,
        )
        return _tool_error(f"search failed: {e}")
    except Exception as e:
        logger.error("Unexpected error searching for %r: %s", query, e)
        return _tool_error(f"search failed: {e}")


@tool
async def get_field_layout(
    class_name: str,
) -> str:
    """Show the memory layout of a C++ class or struct, including field offsets and sizes.

    Args:
        class_name: Fully-qualified class name, e.g. 'mozilla::dom::AudioContext'.

    Returns:
        Field layout as JSON.
    """
    try:
        return await _get_client().search_field_layout(class_name)
    except SearchfoxNetworkError as e:
        return _tool_error(f"field layout fetch failed: {e}")
    except SearchfoxRequestError as e:
        logger.warning("Bad request fetching field layout for %r: %s", class_name, e)
        return _tool_error(f"field layout fetch failed: {e}")
    except Exception as e:
        logger.error("Unexpected error fetching field layout for %r: %s", class_name, e)
        return _tool_error(f"field layout fetch failed: {e}")


@tool
async def get_blame(
    file_path: str,
    lines: list[int],
) -> str:
    """Get the commit that last modified each of the given lines in a file.

    Args:
        file_path: Repository-relative path, e.g. 'dom/media/webaudio/AudioNode.cpp'.
        lines: List of 1-based line numbers to look up.

    Returns:
        For each line: 'LINE: HASH (DATE) MESSAGE'.
    """
    try:
        results = await _get_client().get_blame_for_lines(file_path, lines)
        if not results:
            return "No blame information found."
        return "\n".join(
            f"{line}: {hash_} ({date}) {message}"
            for line, hash_, message, date in results
        )
    except SearchfoxNetworkError as e:
        return _tool_error(f"blame fetch failed: {e}")
    except SearchfoxRequestError as e:
        logger.warning(
            "Bad request fetching blame for %r lines %r: %s", file_path, lines, e
        )
        return _tool_error(f"blame fetch failed: {e}")
    except Exception as e:
        logger.error("Unexpected error fetching blame for %r: %s", file_path, e)
        return _tool_error(f"blame fetch failed: {e}")


@tool
async def check_can_gc(
    symbol: str,
) -> str:
    """Check whether a C++ function can trigger garbage collection in SpiderMonkey.

    Accepts partial names (e.g. 'CreateGain') or fully-qualified names
    (e.g. 'mozilla::dom::AudioContext::CreateGain').

    Args:
        symbol: Function name to check.

    Returns:
        For each match: whether it can GC, and the GC call path if available.
    """
    try:
        results = await _get_client().get_gc_info(symbol)
        if not results:
            return "No GC information found. GC analysis is only available for C++ functions."
        lines = []
        for pretty, _mangled, can_gc, gc_path in results:
            status = "can GC" if can_gc else "cannot GC"
            line = f"{pretty}: {status}"
            if gc_path:
                line += f" (via {gc_path})"
            lines.append(line)
        return "\n".join(lines)
    except SearchfoxNetworkError as e:
        return _tool_error(f"GC check failed: {e}")
    except SearchfoxRequestError as e:
        logger.warning("Bad request checking GC status for %r: %s", symbol, e)
        return _tool_error(f"GC check failed: {e}")
    except Exception as e:
        logger.error("Unexpected error checking GC status for %r: %s", symbol, e)
        return _tool_error(f"GC check failed: {e}")


@tool
async def find_definition(
    name: str,
    path_filter: Optional[str] = None,
) -> str:
    """Find the definition of a function, method, class, or struct.

    Accepts partial names (e.g. 'AudioNode') or fully-qualified names
    (e.g. 'mozilla::dom::AudioNode' or 'AudioNode::Connect').

    Args:
        name: Symbol name to look up.
        path_filter: Optional path prefix, e.g. 'dom/media'.

    Returns:
        The definition source.
    """
    try:
        return await _get_client().get_definition(name, path_filter)
    except SearchfoxNetworkError as e:
        return _tool_error(f"definition lookup failed: {e}")
    except SearchfoxRequestError as e:
        logger.warning(
            "Bad request finding definition for %r (path=%r): %s", name, path_filter, e
        )
        return _tool_error(f"definition lookup failed: {e}")
    except Exception as e:
        logger.error("Unexpected error finding definition for %r: %s", name, e)
        return _tool_error(f"definition lookup failed: {e}")


@tool
async def search_identifier(
    identifier: str,
    path_filter: Optional[str] = None,
    langs: Optional[list[LangStr]] = None,
    tests: Tests = None,
    limit: int = 50,
) -> str:
    """Search for an exact identifier across the codebase.

    Args:
        identifier: Identifier to search for.
        path_filter: Optional path prefix, e.g. 'dom/media'.
        langs: Optional language filter. Multiple values are OR-ed.
        tests: 'only' to restrict to test files, 'exclude' to omit them.
        limit: Maximum number of results (default 50).

    Returns:
        Matching lines as 'path:line: content' entries.
    """
    try:
        results = await _get_client().search(
            id=identifier,
            path=path_filter,
            langs=langs,
            tests=tests,
            limit=limit,
        )
        if not results:
            return "No results found."
        return "\n".join(f"{path}:{line}: {content}" for path, line, content in results)
    except SearchfoxNetworkError as e:
        return _tool_error(f"identifier search failed: {e}")
    except SearchfoxRequestError as e:
        logger.warning(
            "Bad request searching for identifier %r (path=%r, langs=%r): %s",
            identifier,
            path_filter,
            langs,
            e,
        )
        return _tool_error(f"identifier search failed: {e}")
    except Exception as e:
        logger.error("Unexpected error searching for identifier %r: %s", identifier, e)
        return _tool_error(f"identifier search failed: {e}")


@tool
async def calls_from(
    symbol: str,
    depth: int = 2,
) -> str:
    """Find functions called by the given symbol (outgoing calls).

    Args:
        symbol: Fully-qualified function or method name, e.g. 'mozilla::dom::AudioNode::Connect'.
        depth: Levels of calls to traverse (default 2).

    Returns:
        Call graph as JSON.
    """
    try:
        return await _get_client().search_call_graph(calls_from=symbol, depth=depth)
    except SearchfoxNetworkError as e:
        return _tool_error(f"call graph fetch failed: {e}")
    except SearchfoxRequestError as e:
        logger.warning(
            "Bad request fetching calls from %r (depth=%d): %s", symbol, depth, e
        )
        return _tool_error(f"call graph fetch failed: {e}")
    except Exception as e:
        logger.error("Unexpected error fetching calls from %r: %s", symbol, e)
        return _tool_error(f"call graph fetch failed: {e}")


@tool
async def calls_to(
    symbol: str,
    depth: int = 2,
) -> str:
    """Find functions that call the given symbol (incoming calls).

    Args:
        symbol: Fully-qualified function or method name, e.g. 'mozilla::dom::AudioNode::Connect'.
        depth: Levels of callers to traverse (default 2).

    Returns:
        Call graph as JSON.
    """
    try:
        return await _get_client().search_call_graph(calls_to=symbol, depth=depth)
    except SearchfoxNetworkError as e:
        return _tool_error(f"call graph fetch failed: {e}")
    except SearchfoxRequestError as e:
        logger.warning(
            "Bad request fetching calls to %r (depth=%d): %s", symbol, depth, e
        )
        return _tool_error(f"call graph fetch failed: {e}")
    except Exception as e:
        logger.error("Unexpected error fetching calls to %r: %s", symbol, e)
        return _tool_error(f"call graph fetch failed: {e}")


@tool
async def calls_between(
    symbol_a: str,
    symbol_b: str,
    depth: int = 2,
) -> str:
    """Find call paths between two symbols or classes.

    Args:
        symbol_a: First fully-qualified symbol or class name, e.g. 'mozilla::dom::AudioContext'.
        symbol_b: Second fully-qualified symbol or class name.
        depth: Levels to traverse (default 2).

    Returns:
        Call graph as JSON.
    """
    try:
        return await _get_client().search_call_graph(
            calls_between=(symbol_a, symbol_b), depth=depth
        )
    except SearchfoxNetworkError as e:
        return _tool_error(f"call graph fetch failed: {e}")
    except SearchfoxRequestError as e:
        logger.warning(
            "Bad request fetching calls between %r and %r (depth=%d): %s",
            symbol_a,
            symbol_b,
            depth,
            e,
        )
        return _tool_error(f"call graph fetch failed: {e}")
    except Exception as e:
        logger.error(
            "Unexpected error fetching calls between %r and %r: %s",
            symbol_a,
            symbol_b,
            e,
        )
        return _tool_error(f"call graph fetch failed: {e}")


@tool
async def get_function_at_line(
    file_path: str,
    line: int,
) -> str:
    """Get the source of the innermost function enclosing a given line.

    Useful when you know a line number and want the full function body without
    having to know the function name.

    Args:
        file_path: Repository-relative path, e.g. 'dom/media/webaudio/AudioNode.cpp'.
        line: 1-based line number inside the function.

    Returns:
        The function source.
    """
    try:
        return await _get_client().get_function_at_line(file_path, line)
    except SearchfoxNetworkError as e:
        return _tool_error(f"function lookup failed: {e}")
    except SearchfoxRequestError as e:
        logger.warning(
            "Bad request fetching function at line %d in %r: %s", line, file_path, e
        )
        return _tool_error(f"function lookup failed: {e}")
    except Exception as e:
        logger.error(
            "Unexpected error fetching function at line %d in %r: %s",
            line,
            file_path,
            e,
        )
        return _tool_error(f"function lookup failed: {e}")


SEARCHFOX_TOOLS = [
    expand_context,
    find_definition,
    search_text,
    search_identifier,
    get_blame,
    get_field_layout,
    get_function_at_line,
    calls_from,
    calls_to,
    calls_between,
    check_can_gc,
]
