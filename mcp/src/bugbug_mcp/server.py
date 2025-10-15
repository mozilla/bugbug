"""MCP server for code review functionality.

Provides contexts for reviewing patches from Phabricator.
"""

import functools
import os
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastmcp import FastMCP
from fastmcp.resources import FileResource
from pydantic import Field

from bugbug import phabricator, utils
from bugbug.code_search.searchfox_api import FunctionSearchSearchfoxAPI
from bugbug.tools.code_review import Bug, PhabricatorPatch
from bugbug.utils import get_secret

mcp = FastMCP("BugBug Code Review MCP Server")


PATCH_REVIEW_PROMPT = """**Task**:

Generate high-quality code review comments for the patch provided below.

**Instructions**:

1. **Analyze the Changes**:

   * Understand the intent and structure of the changes in the patch.
   * Use the provided summarization for context, but prioritize what's visible in the diff.

2. **Identify Issues**:

   * Detect bugs, logical errors, performance concerns, security issues, or violations of the coding standards.
   * Focus only on **new or changed lines** (lines beginning with `+`).

3. **Assess Confidence**:

   * **Sort the comments by descending confidence and importance**:
     * Start with issues you are **certain are valid**.
     * Also, prioritize important issues that you are **confident about**.
     * Follow with issues that are **plausible but uncertain** (possible false positives).

4. **Write Clear, Constructive Comments**:

   * Use **direct, declarative language**.
   * Keep comments **short and specific**.
   * Focus strictly on code-related concerns.
   * Avoid hedging language (e.g., don't use "maybe", "might want to", or form questions).
   * Avoid repeating what the code is doing unless it supports your critique.

**Avoid Comments That**:

* Refer to unmodified code (lines without a `+` prefix).
* Ask for verification or confirmation (e.g., "Check if…").
* Provide praise or restate obvious facts.
* Focus on testing.


"""

OUTPUT_FORMAT_JSON = """
---

**Output Format**:

Respond only with a **JSON list**. Each object must contain the following fields:

* `"file"`: The relative path to the file the comment applies to.
* `"code_line"`: The number of the specific changed line of code that the comment refers to.
* `"comment"`: A concise review comment.
* `"explanation"`: A brief rationale for the comment, including how confident you are and why.
"""

OUTPUT_FORMAT_TEXT = """
---

**Output Format**:

Respond only with a **plain text list** with the following details:

* `"filename"`: The relative path to the file the comment applies to.
* `"line_number"`: The number of the specific changed line of code that the comment refers to.
* `"comment"`: A concise review comment.

The format should be: filename:line_number "comment"
"""

EXAMPLES = """
---

**Examples**:

{comment_examples}
{approved_examples}

"""


@functools.cache
def get_code_review_tool():
    from langchain_core.runnables import RunnablePassthrough

    from bugbug.tools.code_review import CodeReviewTool, ReviewCommentsDB
    from bugbug.vectordb import QdrantVectorDB

    # FIXME: This is a workaround, we should refactor CodeReviewTool to avoid this.
    class MockLLM(RunnablePassthrough):
        def bind_tools(self, *args, **kwargs):
            return self

    review_comments_db = ReviewCommentsDB(QdrantVectorDB("diff_comments"))

    tool = CodeReviewTool(
        MockLLM(),
        review_comments_db=review_comments_db,
    )

    return tool


async def fetch_patch_data(patch_url: str) -> dict[str, str]:
    """Fetch patch data from the provided URL."""
    # parse the url e.g., https://phabricator.services.mozilla.com/D37960
    parsed_url = urlparse(patch_url)
    if parsed_url.netloc == "phabricator.services.mozilla.com":
        assert parsed_url.path.startswith("/D"), "Invalid Phabricator URL"
        revision_id = int(parsed_url.path[2:])
        revisions = phabricator.get(rev_ids=[int(revision_id)])
        assert len(revisions) == 1

        patch = PhabricatorPatch(revisions[0]["fields"]["diffID"])
        review_tool = get_code_review_tool()

        return {
            "patch": patch.raw_diff,
            "bug_title": patch.bug_title,
            "patch_title": patch.patch_title,
            "target_software": "Mozilla Firefox",
            "examples": EXAMPLES.format(
                comment_examples=review_tool._get_comment_examples(patch),
                approved_examples=review_tool._get_generated_examples(patch),
            ),
        }

    else:
        raise ValueError(f"Unsupported patch URL: {patch_url}")


@mcp.prompt()
async def patch_review(
    patch_url: str = Field(description="URL to the Phabricator patch to review."),
) -> str:
    """Review a code patch from Phabricator."""
    try:
        patch_data = await fetch_patch_data(patch_url)
    except Exception as e:
        raise ValueError(f"Failed to fetch patch data from {patch_url}: {str(e)}")

    context_section = f"""
---

**Review Context**:

Target Software: {patch_data["target_software"]}
Bug Title: {patch_data["bug_title"]}
Patch Title: {patch_data["patch_title"]}
Source URL: {patch_url}

**Patch to Review**:

{patch_data["patch"]}
"""

    final_prompt = (
        PATCH_REVIEW_PROMPT
        + OUTPUT_FORMAT_TEXT
        + patch_data["examples"]
        + context_section
    )

    return final_prompt


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


@mcp.resource(
    uri="docs://{doc_path*}",
    name="Content from Documentation",
    mime_type="text/markdown",
)
async def handle_doc_view_resource(doc_path: str) -> str:
    """Retrieve the content of a section from Firefox Source Tree Documentation."""
    url = f"https://firefox-source-docs.mozilla.org/_sources/{doc_path}.txt"

    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def main():
    phabricator.set_api_key(
        get_secret("PHABRICATOR_URL"), get_secret("PHABRICATOR_TOKEN")
    )

    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8080)),
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
