import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from unidiff import PatchSet

from bugbug.tools.code_review import data_types, langchain_tools
from bugbug.tools.code_review.data_types import Skill, _strip_frontmatter
from bugbug.tools.code_review.langchain_tools import create_load_skill_tool
from bugbug.tools.code_review.utils import find_comment_scope

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures/phabricator")


def test_find_comment_scope():
    test_data = {
        (233024, 964198): {
            "browser/components/newtab/test/browser/browser.toml": {
                79: {
                    "line_start": 78,
                    "line_end": 79,
                    "has_added_lines": False,
                }
            },
            "browser/components/asrouter/tests/browser/browser.toml": {
                63: {
                    "line_start": 60,
                    "line_end": 74,
                    "has_added_lines": True,
                },
            },
        },
        (240754, 995999): {
            "dom/canvas/WebGLShaderValidator.cpp": {
                39: {
                    "line_start": 37,
                    "line_end": 42,
                    "has_added_lines": True,
                },
                46: {
                    "line_start": 37,
                    "line_end": 42,
                    "has_added_lines": True,
                },
            }
        },
    }

    for (revision_id, diff_id), patch_files in test_data.items():
        with open(os.path.join(FIXTURES_DIR, f"D{revision_id}-{diff_id}.diff")) as f:
            raw_diff = f.read()

        patch_set = PatchSet.from_string(raw_diff)

        for file_name, target_hunks in patch_files.items():
            patched_file = next(
                patched_file
                for patched_file in patch_set
                if patched_file.path == file_name
            )

            for line_number, expected_scope in target_hunks.items():
                assert find_comment_scope(patched_file, line_number) == expected_scope


def _mock_client_returning(text: str) -> MagicMock:
    response = MagicMock()
    response.text = text
    response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    return client


def test_strip_frontmatter_present():
    text = "---\nname: mozilla\ndescription: foo\n---\nThe body.\n"
    assert _strip_frontmatter(text) == "The body.\n"


def test_strip_frontmatter_absent():
    text = "Just a body, no frontmatter.\n"
    assert _strip_frontmatter(text) == text


def test_strip_frontmatter_unterminated():
    text = "---\nname: mozilla\nstill no closing marker\n"
    assert _strip_frontmatter(text) == text


@pytest.mark.asyncio
async def test_skill_load_caches():
    skill = Skill(
        name="a",
        url="https://example.com/a.md",
        description="example",
    )
    client = _mock_client_returning("---\nname: a\n---\nbody\n")
    with patch.object(data_types, "get_http_client", return_value=client):
        first = await skill.load()
        second = await skill.load()
    assert first == "body\n"
    assert second == "body\n"
    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_load_skill_unknown_name():
    skills = [
        Skill(
            name="mozilla-style",
            url="https://example.com/mozilla-style.md",
            description="Mozilla style guide",
        )
    ]
    tool = create_load_skill_tool(skills)
    client = _mock_client_returning("ignored")
    with patch.object(data_types, "get_http_client", return_value=client):
        result = await tool.ainvoke({"name": "nonexistent"})
    assert "Unknown skill 'nonexistent'" in result
    assert "mozilla-style" in result
    assert client.get.await_count == 0


@pytest.mark.asyncio
async def test_load_skill_happy_path():
    skills = [
        Skill(
            name="mozilla-style",
            url="https://example.com/mozilla-style.md",
            description="Mozilla style guide",
        )
    ]
    tool = create_load_skill_tool(skills)
    client = _mock_client_returning(
        "---\nname: mozilla-style\ndescription: ignored\n---\nUse 4-space indents.\n"
    )
    with patch.object(data_types, "get_http_client", return_value=client):
        result = await tool.ainvoke({"name": "mozilla-style"})
    assert result == "Use 4-space indents.\n"


@pytest.mark.asyncio
async def test_load_skill_fetch_failure(caplog):
    skills = [
        Skill(
            name="mozilla-style",
            url="https://example.com/mozilla-style.md",
            description="Mozilla style guide",
        )
    ]
    tool = create_load_skill_tool(skills)
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
    with (
        patch.object(data_types, "get_http_client", return_value=client),
        caplog.at_level(logging.ERROR, logger=langchain_tools.logger.name),
    ):
        result = await tool.ainvoke({"name": "mozilla-style"})
    assert "Failed to load skill 'mozilla-style'" in result
    assert any(
        "Failed to load skill 'mozilla-style'" in record.message
        for record in caplog.records
    )


def test_load_skill_tool_description_lists_skills():
    skills = [
        Skill(
            name="mozilla-style",
            url="https://example.com/mozilla-style.md",
            description="Mozilla style guide",
        ),
        Skill(
            name="security-checklist",
            url="https://example.com/sec.md",
            description="Common security pitfalls",
        ),
    ]
    tool = create_load_skill_tool(skills)
    for skill in skills:
        assert skill.name in tool.description
        assert skill.description in tool.description
