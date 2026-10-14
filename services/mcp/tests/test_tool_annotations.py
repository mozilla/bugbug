"""Tests that MCP tools are annotated as read-only where applicable."""

import pytest

# bugzilla_quick_search is excluded: it's disabled via mcp.disable() due to
# https://github.com/mozilla/bugbug/issues/5890, which hides it from get_tool()
# too. Its @mcp.tool(annotations=...) is still set for when it's re-enabled.
READ_ONLY_TOOLS = {
    "get_bugzilla_bug",
    "get_phabricator_revision",
    "read_fx_doc_section",
}


@pytest.mark.parametrize("name", sorted(READ_ONLY_TOOLS))
async def test_read_only_tools_have_read_only_hint(name):
    from bugbug_mcp.server import mcp

    tool = await mcp.get_tool(name)
    assert tool.annotations is not None, f"{name} has no annotations"
    assert tool.annotations.readOnlyHint is True, (
        f"{name} is not marked with readOnlyHint=True"
    )
