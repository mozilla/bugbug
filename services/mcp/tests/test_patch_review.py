from unittest.mock import AsyncMock, Mock

import pytest
from bugbug_mcp.server import mcp
from fastmcp.client import Client


@pytest.fixture
def review_dependencies(mocker):
    patch = Mock()
    patch.load = AsyncMock()
    patch.github_repo_ref = AsyncMock(return_value=("inferred/repo", "release"))
    mocker.patch("bugbug_mcp.server.PhabricatorPatch", return_value=patch)
    tool = Mock(target_software="Firefox")
    tool.patch_summarizer.run.return_value = "summary"
    tool.generate_initial_prompt.side_effect = lambda patch, summary, context: context
    mocker.patch("bugbug_mcp.server.get_code_review_tool", return_value=tool)
    loader = mocker.patch(
        "bugbug.tools.code_review.review_context.load_external_context_for_review",
        new_callable=AsyncMock,
        return_value=("<external_context>skill</external_context>", []),
    )
    return patch, loader


async def test_patch_review_context_via_mcp(review_dependencies):
    patch, loader = review_dependencies
    async with Client(mcp) as client:
        result = await client.get_prompt(
            "patch_review",
            {
                "patch_url": "https://phabricator.services.mozilla.com/D123",
                "review_context_repo": "example/repo",
                "review_context_branch": "dev",
                "extra_context_toml": "rules",
                "content_overrides": '{"skill.md": "contents"}',
            },
        )
    assert (
        "<external_context>skill</external_context>" in result.messages[0].content.text
    )
    loader.assert_awaited_once_with(
        patch,
        "example/repo",
        review_context_branch="dev",
        extra_context_toml="rules",
        content_overrides={"skill.md": "contents"},
    )
    patch.github_repo_ref.assert_not_awaited()


async def test_patch_review_infers_repository(review_dependencies):
    patch, loader = review_dependencies
    async with Client(mcp) as client:
        await client.get_prompt(
            "patch_review",
            {"patch_url": "https://phabricator.services.mozilla.com/D123"},
        )
    loader.assert_awaited_once_with(
        patch,
        "inferred/repo",
        review_context_branch="release",
        extra_context_toml=None,
        content_overrides=None,
    )


@pytest.mark.parametrize("overrides", ["{", "[]", '{"skill": 1}'])
async def test_patch_review_rejects_invalid_overrides(review_dependencies, overrides):
    async with Client(mcp) as client:
        with pytest.raises(
            Exception, match="content_overrides must be a JSON object of strings"
        ):
            await client.get_prompt(
                "patch_review",
                {
                    "patch_url": "https://phabricator.services.mozilla.com/D123",
                    "content_overrides": overrides,
                },
            )
    review_dependencies[1].assert_not_awaited()
