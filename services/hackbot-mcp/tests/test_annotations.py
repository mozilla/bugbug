"""Tool annotations: the client's permission prompt is the user's confirmation."""

import pytest

READ_ONLY = ["get_artifact_url", "get_result", "get_run", "list_agents", "list_my_runs"]


async def test_every_tool_is_covered(server):
    names = {tool.name for tool in await server.list_tools()}
    assert names == {*READ_ONLY, "start_run", "apply_actions"}


@pytest.mark.parametrize("name", READ_ONLY)
async def test_reads_are_read_only(name, server):
    tool = await server.get_tool(name)
    assert tool.annotations.readOnlyHint is True


async def test_start_run_is_not_read_only(server):
    tool = await server.get_tool("start_run")
    assert tool.annotations.readOnlyHint is False
    # With auto_apply it can act on Bugzilla and Phabricator, so it doesn't
    # claim to be non-destructive.
    assert tool.annotations.destructiveHint is not False


async def test_apply_actions_is_destructive(server):
    tool = await server.get_tool("apply_actions")
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is True


async def test_instructions_cover_the_workflow(server):
    instructions = server.instructions
    for phrase in (
        "fresh checkout",
        "get their confirmation",
        "get_run",
        "agent's claims",
        "explicit approval",
        "cannot be undone",
        "git am",
    ):
        assert phrase in instructions
