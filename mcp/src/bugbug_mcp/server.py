"""MCP server for Firefox Development."""

import functools
import os
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.resources import FileResource
from pydantic import Field

from bugbug import utils
from bugbug.code_search.searchfox_api import FunctionSearchSearchfoxAPI
from bugbug.tools.code_review.prompts import SYSTEM_PROMPT_TEMPLATE
from bugbug.tools.core.platforms.bugzilla import SanitizedBug
from bugbug.tools.core.platforms.phabricator import (
    PhabricatorPatch,
    SanitizedPhabricatorPatch,
)
from bugbug.utils import get_secret

os.environ["OPENAI_API_KEY"] = get_secret("OPENAI_API_KEY")

mcp = FastMCP("Firefox Development MCP Server")


@functools.cache
def get_code_review_tool():
    from bugbug.tools.code_review.agent import CodeReviewTool

    return CodeReviewTool.create()


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
    else:
        raise ValueError(f"Unsupported patch URL: {patch_url}")

    patch = PhabricatorPatch(revision_id=revision_id)

    tool = get_code_review_tool()
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        target_software=tool.target_software,
    )
    patch_summary = tool.patch_summarizer.run(patch)
    initial_prompt = tool.generate_initial_prompt(patch, patch_summary)

    return system_prompt + "\n\n" + initial_prompt


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
    return SanitizedBug.get(bug_id).to_md()


@mcp.tool()
def get_bugzilla_bug(bug_id: int) -> str:
    """Retrieve a bug from Bugzilla alongside its change history and comments."""
    return SanitizedBug.get(bug_id).to_md()


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
