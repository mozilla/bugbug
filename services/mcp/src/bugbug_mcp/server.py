"""MCP server for Firefox Development."""

import functools
import json
import os
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.resources import FileResource
from mcp.types import ToolAnnotations

from bugbug.tools.code_review.prompts import SYSTEM_PROMPT_TEMPLATE
from bugbug.tools.core.platforms.bugzilla import SanitizedBug
from bugbug.tools.core.platforms.phabricator import (
    PhabricatorPatch,
    SanitizedPhabricatorPatch,
)

mcp = FastMCP("Firefox Development MCP Server")

# Temporarily disabled due to https://github.com/mozilla/bugbug/issues/5890.
# Once that issue is resolved, we should re-enable the Bugzilla search tool.
mcp.disable(names={"bugzilla_quick_search"})


@functools.cache
def get_code_review_tool():
    from bugbug.tools.code_review.agent import CodeReviewTool

    return CodeReviewTool.create()


def _revision_id_from_url(patch_url: str) -> int:
    parsed_url = urlparse(patch_url)
    if (
        parsed_url.netloc == "phabricator.services.mozilla.com"
        and parsed_url.path.startswith("/D")
    ):
        return int(parsed_url.path[2:])
    raise ValueError(f"Unsupported patch URL: {patch_url}")


def _parse_content_overrides(content_overrides: str | None) -> dict[str, str] | None:
    if content_overrides is None:
        return None
    try:
        overrides = json.loads(content_overrides)
        if not isinstance(overrides, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in overrides.items()
        ):
            raise ValueError("expected an object of strings")
    except (ValueError, TypeError) as exc:
        raise ValueError("content_overrides must be a JSON object of strings") from exc
    return overrides


@mcp.prompt()
async def patch_review(
    patch_url: str,
    review_context_repo: str | None = None,
    review_context_branch: str | None = None,
    extra_context_toml: str | None = None,
    content_overrides: str | None = None,
) -> str:
    """Review a code patch from Phabricator."""
    revision_id = _revision_id_from_url(patch_url)
    overrides = _parse_content_overrides(content_overrides)
    patch = PhabricatorPatch(revision_id=revision_id)
    await patch.load()

    tool = get_code_review_tool()
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        target_software=tool.target_software,
    )
    patch_summary = tool.patch_summarizer.run(patch)
    from bugbug.tools.code_review.review_context import (
        resolve_external_context_for_review,
    )

    external_context, _ = await resolve_external_context_for_review(
        patch,
        review_context_repo,
        review_context_branch=review_context_branch,
        extra_context_toml=extra_context_toml,
        content_overrides=overrides,
    )
    initial_prompt = tool.generate_initial_prompt(
        patch, patch_summary, external_context
    )

    return system_prompt + "\n\n" + initial_prompt


@mcp.resource(
    uri="bugzilla://bug/{bug_id}",
    name="Bugzilla Bug Content",
    mime_type="text/markdown",
)
def handle_bug_view_resource(bug_id: int) -> str:
    """Retrieve a bug from Bugzilla alongside its change history and comments."""
    return SanitizedBug.get(bug_id).to_md()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_bugzilla_bug(bug_id: int) -> str:
    """Retrieve a bug from Bugzilla alongside its change history and comments."""
    return SanitizedBug.get(bug_id).to_md()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def bugzilla_quick_search(
    search_query: Annotated[
        str,
        "A quick search string to find bugs. Can include bug numbers, keywords, status, product, component, etc. Examples: 'firefox crash', 'FIXED', 'status:NEW product:Core'",
    ],
    limit: Annotated[int, "Maximum number of bugs to return (default: 20)"] = 20,
) -> str:
    """Search for bugs in Bugzilla using quick search syntax.

    Quick search supports shortcuts like bug numbers, keywords, status,
    products/components, and combinations of these.

    For the full syntax reference, see:
    https://bugzilla.mozilla.org/page.cgi?id=quicksearch.html

    Returns a formatted list of matching bugs with their ID, status, summary, and link.
    """
    from libmozdata.bugzilla import Bugzilla

    bugs = []

    def bughandler(bug):
        bugs.append(bug)

    # Use Bugzilla quicksearch API
    params = {
        "quicksearch": search_query,
        "limit": limit,
    }

    Bugzilla(
        params,
        include_fields=[
            "id",
            "status",
            "summary",
            "product",
            "component",
            "priority",
            "severity",
        ],
        bughandler=bughandler,
    ).get_data().wait()

    if not bugs:
        return f"No bugs found matching: {search_query}"

    # Format results concisely for LLM consumption
    result = f"Found {len(bugs)} bug(s) matching '{search_query}':\n\n"

    for bug in bugs:
        bug_id = bug["id"]
        status = bug.get("status", "N/A")
        summary = bug.get("summary", "N/A")
        product = bug.get("product", "N/A")
        component = bug.get("component", "N/A")
        priority = bug.get("priority", "N/A")
        severity = bug.get("severity", "N/A")

        result += f"Bug {bug_id} [{status}] - {summary}\n"
        result += f"  Product: {product}::{component}\n"
        result += f"  Priority: {priority} | Severity: {severity}\n"
        result += f"  URL: https://bugzilla.mozilla.org/show_bug.cgi?id={bug_id}\n\n"

    return result


async def _get_revision_md(revision_id: int) -> str:
    patch = SanitizedPhabricatorPatch(revision_id=revision_id)
    if not await patch.is_accessible() or not patch.is_public():
        raise ToolError(
            f"Revision D{revision_id} was not found. It may not exist or may be private."
        )
    return await patch.to_md()


@mcp.resource(
    uri="phabricator://revision/D{revision_id}",
    name="Phabricator Revision Content",
    mime_type="text/markdown",
)
async def handle_revision_view_resource(revision_id: int) -> str:
    """Retrieve a revision from Phabricator alongside its comments."""
    return await _get_revision_md(revision_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_phabricator_revision(revision_id: int) -> str:
    """Retrieve a revision from Phabricator alongside its comments."""
    return await _get_revision_md(revision_id)


llms_txt = FileResource(
    uri="docs://llms.txt",
    path=Path("./static/llms.txt").resolve(),
    name="Firefox Source Docs (llms.txt)",
    description="Firefox Source Tree Documentation which helps with Firefox related workflows and troubleshooting. You must use it to understand questions about Firefox development, architecture, and best practices before trying to search anywhere else. You need to read the relevant sections to get enough context to perform your task.",
    mime_type="text/markdown",
)
mcp.add_resource(llms_txt)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def read_fx_doc_section(
    doc_path: Annotated[
        str,
        "The path to a documentation section to read as listed in the docs://llms.txt resource on this MCP",
    ],
) -> str:
    """Retrieve the content of a section from Firefox Source Tree Documentation."""
    if not doc_path.endswith(".md") and not doc_path.endswith(".rst"):
        raise ToolError(
            f"Invalid path: {doc_path}. Path must end with `.rst` or `.md`. Use the docs://llms.txt resource on this MCP to find valid paths."
        )

    url = f"https://firefox-source-docs.mozilla.org/_sources/{doc_path}.txt"

    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code == 404:
            raise ToolError(
                f"Invalid path: {doc_path}. Use the docs://llms.txt resource on this MCP to find valid paths."
            )

        response.raise_for_status()

        return response.text


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def read_review_guideline(
    patch_url: Annotated[
        str, "The URL of the patch under review, as given to the patch_review prompt"
    ],
    file_path: Annotated[
        str,
        "Repository-relative path of a file the review guidelines point to, e.g. 'accessible/docs/HCMCSSChecklist.md'",
    ],
    review_context_repo: str | None = None,
    review_context_branch: str | None = None,
    content_overrides: str | None = None,
) -> str:
    """Read a file that the review guidelines of the patch_review prompt point to.

    The file is read at the same version as the guidelines, which can be newer
    than the code the patch is based on. Pass the same review_context_repo,
    review_context_branch and content_overrides as to the patch_review prompt,
    if any.
    """
    from bugbug.tools.code_review.review_context import (
        fetch_review_guideline,
        resolve_review_context_source,
    )

    patch = PhabricatorPatch(revision_id=_revision_id_from_url(patch_url))
    await patch.load()
    source = await resolve_review_context_source(
        patch, review_context_repo, review_context_branch
    )
    if source is None:
        raise ToolError("No review guidelines are configured for this patch.")
    try:
        return await fetch_review_guideline(
            source, file_path, _parse_content_overrides(content_overrides)
        )
    except FileNotFoundError as e:
        raise ToolError(str(e)) from e


def main():
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8080)),
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
