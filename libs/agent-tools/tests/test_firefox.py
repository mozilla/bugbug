"""Tests for the Firefox tools."""

from agent_tools import firefox
from agent_tools.claude_sdk import build_sdk_server
from mcp.types import ListToolsRequest


async def _list(server):
    return (
        await server.request_handlers[ListToolsRequest](
            ListToolsRequest(method="tools/list")
        )
    ).root.tools


async def test_exposes_firefox_tools(tmp_path):
    ctx = firefox.FirefoxContext.from_source_repo(tmp_path)
    config = build_sdk_server("firefox", ctx, firefox.TOOLS)
    assert config["type"] == "sdk"
    tools = await _list(config["instance"])
    assert {t.name for t in tools} == {
        "evaluate_testcase",
        "build_firefox",
        "evaluate_js_shell",
        "bootstrap_firefox",
    }
