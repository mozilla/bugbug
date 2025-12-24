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

from bugbug import phabricator, utils
from bugbug.code_search.searchfox_api import FunctionSearchSearchfoxAPI
from bugbug.tools.core.platforms.bugzilla import Bug
from bugbug.tools.core.platforms.phabricator import PhabricatorPatch
from bugbug.utils import get_secret

logger = logging.getLogger(__name__)

# Set OPENAI_API_KEY if available (optional for most MCP tools)
try:
    os.environ["OPENAI_API_KEY"] = get_secret("OPENAI_API_KEY")
except ValueError:
    # API key not found, will skip code review features
    logger.warning(
        "OPENAI_API_KEY not found. Code review features will be unavailable."
    )

mcp = FastMCP("Firefox Development MCP Server")


@functools.cache
def get_code_review_tool():
    from langchain.chat_models import init_chat_model

    from bugbug.tools.code_review import CodeReviewTool, ReviewCommentsDB
    from bugbug.vectordb import QdrantVectorDB

    return CodeReviewTool(
        llm=init_chat_model("gpt-5.1"),
        review_comments_db=ReviewCommentsDB(QdrantVectorDB("diff_comments")),
    )


@mcp.prompt()
async def patch_review(
    patch_url: str = Field(description="URL to the Phabricator patch to review."),
) -> str:
    """Review a code patch from Phabricator."""
    parsed_url = urlparse(patch_url)
    if (
        parsed_url.netloc == "phabricator.services.mozilla.com"
        and parsed_url.path.startswith("/D")
    ):
        revision_id = int(parsed_url.path[2:])
        revisions = phabricator.get(rev_ids=[int(revision_id)])
        assert len(revisions) == 1

        patch = PhabricatorPatch(revisions[0]["fields"]["diffID"])

    else:
        raise ValueError(f"Unsupported patch URL: {patch_url}")

    # FIXME: add the system prompt as well
    return get_code_review_tool().generate_initial_prompt(patch)


def get_file(commit_hash, path):
    if commit_hash == "main":
        commit_hash = "refs/heads/main"

    r = utils.get_session("githubusercontent").get(
        f"https://raw.githubusercontent.com/mozilla-firefox/firefox/{commit_hash}/{path}",
        headers={
            "User-Agent": utils.get_user_agent(),
        },
    )
    r.raise_for_status()
    return r.text


function_search = FunctionSearchSearchfoxAPI(get_file)


@mcp.tool()
def find_function_definition(
    function_name: Annotated[str, "The name of the function to find its definition."],
) -> str:
    """Find the definition of a function in the Firefox codebase using Searchfox."""
    functions = function_search.get_function_by_name(
        "main",
        "n/a",  # The file path is not used
        function_name,
    )

    if not functions:
        return "Function definition not found."

    return functions[0].source


@mcp.resource(
    uri="bugzilla://bug/{bug_id}",
    name="Bugzilla Bug Content",
    mime_type="text/markdown",
)
def handle_bug_view_resource(bug_id: int) -> str:
    """Retrieve a bug from Bugzilla alongside its change history and comments."""
    return Bug.get(bug_id).to_md()


@mcp.tool()
def get_bugzilla_bug(bug_id: int) -> str:
    """Retrieve a bug from Bugzilla alongside its change history and comments."""
    return Bug.get(bug_id).to_md()


@mcp.tool()
def bugzilla_quick_search(
    search_query: Annotated[
        str,
        "A quick search string to find bugs. Can include bug numbers, keywords, status, product, component, etc. Examples: 'firefox crash', 'FIXED', 'status:NEW product:Core'",
    ],
    limit: Annotated[int, "Maximum number of bugs to return (default: 20)"] = 20,
) -> str:
    """Search for bugs in Bugzilla using quick search syntax.

    Quick search allows natural language searches and supports various shortcuts:
    - Bug numbers: "12345" or "bug 12345"
    - Keywords: "crash", "regression"
    - Status: "NEW", "FIXED", "ASSIGNED"
    - Products/Components: "Firefox", "Core::DOM"
    - Combinations: "firefox crash NEW"

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
    return PhabricatorPatch(revision_id=revision_id).to_md()


@mcp.tool()
def get_phabricator_revision(revision_id: int) -> str:
    """Retrieve a revision from Phabricator alongside its comments."""
    return PhabricatorPatch(revision_id=revision_id).to_md()


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
    # Set Phabricator API key if available (optional)
    try:
        phabricator.set_api_key(
            get_secret("PHABRICATOR_URL"), get_secret("PHABRICATOR_TOKEN")
        )
    except ValueError:
        # Phabricator secrets not available, will skip Phabricator features
        logger.warning(
            "PHABRICATOR_URL or PHABRICATOR_TOKEN not found. "
            "Phabricator features will be unavailable."
        )

    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8080)),
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
