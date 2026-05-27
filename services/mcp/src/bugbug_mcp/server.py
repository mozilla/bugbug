"""MCP server for Firefox Development."""

import functools
import logging
import os
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.resources import FileResource
from pydantic import Field

from bugbug.tools.code_review.data_types import LocalPatch
from bugbug.tools.code_review.prompts import (
    LOCAL_SYSTEM_PROMPT_TEMPLATE,
    SYSTEM_PROMPT_TEMPLATE,
)
from bugbug.tools.core.platforms.bugzilla import SanitizedBug
from bugbug.tools.core.platforms.phabricator import (
    PhabricatorPatch,
    SanitizedPhabricatorPatch,
)

mcp = FastMCP("Firefox Development MCP Server")
logger = logging.getLogger(__name__)


@functools.cache
def get_code_review_tool():
    from bugbug.tools.code_review.agent import CodeReviewTool

    return CodeReviewTool.create()


@functools.cache
def get_local_code_review_tool():
    """Minimal tool for local diff prompt assembly — no LLM calls on the server."""
    from bugbug.tools.code_review.agent import CodeReviewTool

    return CodeReviewTool.create(
        review_comments_db=None,
        suggestion_filterer=None,
    )


async def _patch_review_impl(
    patch_url: str | None,
    diff: str | None,
    commit_message: str | None,
) -> str:
    if diff:
        patch = LocalPatch(diff, commit_message=commit_message or "")
    elif patch_url:
        parsed_url = urlparse(patch_url)
        if (
            parsed_url.netloc == "phabricator.services.mozilla.com"
            and parsed_url.path.startswith("/D")
        ):
            revision_id = int(parsed_url.path[2:])
        else:
            raise ValueError(f"Unsupported patch URL: {patch_url}")
        patch = PhabricatorPatch(revision_id=revision_id)
    else:
        raise ValueError("Provide either patch_url or diff.")

    tool = get_local_code_review_tool() if diff else get_code_review_tool()
    prompt_template = LOCAL_SYSTEM_PROMPT_TEMPLATE if diff else SYSTEM_PROMPT_TEMPLATE
    system_prompt = prompt_template.format(target_software=tool.target_software)
    patch_summary = "" if diff else tool.patch_summarizer.run(patch)
    initial_prompt = tool.generate_initial_prompt(patch, patch_summary)
    return system_prompt + "\n\n" + initial_prompt


@mcp.prompt()
async def patch_review(
    patch_url: str | None = Field(
        default=None,
        description="URL to the Phabricator patch to review.",
    ),
    diff: str | None = Field(
        default=None,
        description="Raw unified diff to review (for local patches not yet on Phabricator).",
    ),
    commit_message: str | None = Field(
        default=None,
        description="Commit message for the local diff (optional, used to extract bug ID, title, and Differential Revision URL).",
    ),
) -> str:
    """Review a code patch from Phabricator or a raw local diff."""
    return await _patch_review_impl(patch_url, diff, commit_message)


@mcp.tool()
async def patch_review_tool(
    patch_url: str | None = None,
    diff: str | None = None,
    commit_message: str | None = None,
) -> str:
    """Build the patch review prompt. Use diff= for local diffs, patch_url= for Phabricator."""
    return await _patch_review_impl(patch_url, diff, commit_message)


@mcp.resource(
    uri="bugzilla://bug/{bug_id}",
    name="Bugzilla Bug Content",
    mime_type="text/markdown",
)
def handle_bug_view_resource(bug_id: int) -> str:
    """Retrieve a bug from Bugzilla alongside its change history and comments."""
    return SanitizedBug.get(bug_id).to_md()


@mcp.tool()
def get_bugzilla_bug(bug_id: int) -> str:
    """Retrieve a bug from Bugzilla alongside its change history and comments."""
    return SanitizedBug.get(bug_id).to_md()


@mcp.tool()
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


@mcp.resource(
    uri="phabricator://revision/D{revision_id}",
    name="Phabricator Revision Content",
    mime_type="text/markdown",
)
def handle_revision_view_resource(revision_id: int) -> str:
    """Retrieve a revision from Phabricator alongside its comments."""
    return SanitizedPhabricatorPatch(revision_id=revision_id).to_md()


@mcp.tool()
def get_phabricator_revision(revision_id: int) -> str:
    """Retrieve a revision from Phabricator alongside its comments."""
    return SanitizedPhabricatorPatch(revision_id=revision_id).to_md()


llms_txt = FileResource(
    uri="docs://llms.txt",
    path=Path("./static/llms.txt").resolve(),
    name="Firefox Source Docs (llms.txt)",
    description="Firefox Source Tree Documentation which helps with Firefox related workflows and troubleshooting. You must use it to understand questions about Firefox development, architecture, and best practices before trying to search anywhere else. You need to read the relevant sections to get enough context to perform your task.",
    mime_type="text/markdown",
)
mcp.add_resource(llms_txt)


@mcp.tool()
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


def main():
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8080)),
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
