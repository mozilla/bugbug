import asyncio
import logging
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from unidiff import PatchSet

from bugbug.tools.code_review import data_types, langchain_tools
from bugbug.tools.code_review.data_types import Skill, _strip_frontmatter
from bugbug.tools.code_review.langchain_tools import _fetch_file, create_load_skill_tool
from bugbug.tools.code_review.utils import find_comment_scope
from bugbug.tools.core.platforms.patch_apply import (
    apply_patched_file,
    get_file_after_stack,
    strip_diff_prefix,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures/phabricator")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_patch(raw_diff):
    patch_set = PatchSet.from_string(raw_diff)
    return SimpleNamespace(patch_set=patch_set, patch_stack=[patch_set])


def make_patch_stack(*raw_diffs):
    patch_stack = [PatchSet.from_string(raw_diff) for raw_diff in raw_diffs]
    return SimpleNamespace(patch_set=patch_stack[-1], patch_stack=patch_stack)


def make_fetch(files):
    async def fetch(path):
        return files[path]

    return fetch


# ---------------------------------------------------------------------------
# strip_diff_prefix
# ---------------------------------------------------------------------------


def test_strip_diff_prefix_removes_a():
    assert strip_diff_prefix("a/foo/bar.h") == "foo/bar.h"


def test_strip_diff_prefix_removes_b():
    assert strip_diff_prefix("b/foo/bar.h") == "foo/bar.h"


def test_strip_diff_prefix_noop():
    assert strip_diff_prefix("foo/bar.h") == "foo/bar.h"


# ---------------------------------------------------------------------------
# apply_patched_file
# ---------------------------------------------------------------------------


def test_apply_patched_file_modifies_lines():
    ps = PatchSet.from_string(
        "--- a/f.txt\n+++ b/f.txt\n@@ -1,3 +1,4 @@\n a\n-b\n+B\n c\n+d\n"
    )
    assert apply_patched_file("a\nb\nc\n", ps[0]) == "a\nB\nc\nd\n"


def test_apply_patched_file_added_file():
    ps = PatchSet.from_string("--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1,2 @@\n+a\n+b\n")
    assert apply_patched_file("", ps[0]) == "a\nb\n"


def test_apply_patched_file_removed_file_raises():
    ps = PatchSet.from_string("--- a/f.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n")
    try:
        apply_patched_file("a\nb\n", ps[0])
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_apply_patched_file_multiple_hunks():
    ps = PatchSet.from_string(
        "--- a/f.txt\n+++ b/f.txt\n"
        "@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
        "@@ -5,2 +5,2 @@\n e\n-f\n+F\n"
    )
    assert apply_patched_file("a\nb\nc\nd\ne\nf\n", ps[0]) == "a\nB\nc\nd\ne\nF\n"


# ---------------------------------------------------------------------------
# get_file_after_stack
# ---------------------------------------------------------------------------


def test_get_file_after_stack_modifies_file():
    patch = make_patch(
        "--- a/foo.txt\n+++ b/foo.txt\n@@ -1,3 +1,4 @@\n a\n-b\n+B\n c\n+d\n"
    )
    result = asyncio.run(
        get_file_after_stack(
            patch.patch_stack, "foo.txt", make_fetch({"foo.txt": "a\nb\nc\n"})
        )
    )
    assert result == "a\nB\nc\nd\n"


def test_get_file_after_stack_added_file():
    patch = make_patch("--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1,2 @@\n+a\n+b\n")
    result = asyncio.run(
        get_file_after_stack(patch.patch_stack, "new.txt", make_fetch({}))
    )
    assert result == "a\nb\n"


def test_get_file_after_stack_unmodified_file():
    patch = make_patch("")
    result = asyncio.run(
        get_file_after_stack(
            patch.patch_stack, "foo.txt", make_fetch({"foo.txt": "a\nb\n"})
        )
    )
    assert result == "a\nb\n"


def test_get_file_after_stack_applies_stack():
    patch = make_patch_stack(
        "--- /dev/null\n+++ b/f.txt\n@@ -0,0 +1,2 @@\n+a\n+b\n",
        "--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,3 @@\n a\n-b\n+B\n+c\n",
    )
    result = asyncio.run(
        get_file_after_stack(patch.patch_stack, "f.txt", make_fetch({}))
    )
    assert result == "a\nB\nc\n"


def test_get_file_after_stack_raises_for_deleted_file():
    patch = make_patch("--- a/f.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n")
    try:
        asyncio.run(
            get_file_after_stack(
                patch.patch_stack, "f.txt", make_fetch({"f.txt": "a\nb\n"})
            )
        )
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_get_file_after_stack_follows_renames():
    patch = make_patch_stack(
        "--- a/old.txt\n+++ b/old.txt\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n",
        "--- a/old.txt\n+++ b/new.txt\n@@ -1,2 +1,3 @@\n a\n-B\n+C\n+d\n",
    )
    result = asyncio.run(
        get_file_after_stack(
            patch.patch_stack, "new.txt", make_fetch({"old.txt": "a\nb\n"})
        )
    )
    assert result == "a\nC\nd\n"


# ---------------------------------------------------------------------------
# phabricator patch_stack non-linear bailout
# ---------------------------------------------------------------------------


def test_patch_stack_bails_on_nonlinear_graph():
    from bugbug.tools.core.platforms.phabricator import PhabricatorPatch

    class FakePatch(PhabricatorPatch):
        def __init__(self):
            pass

        @property
        def _revision_metadata(self):
            return {"phid": "PHID-D"}

        @property
        def stack_graph(self):
            return {
                "PHID-A": [],
                "PHID-B": ["PHID-A"],
                "PHID-C": ["PHID-A"],  # two children of A → non-linear
                "PHID-D": ["PHID-B", "PHID-C"],  # diamond
            }

        @property
        def patch_set(self):
            return PatchSet.from_string("")

    fake = FakePatch()
    try:
        fake.patch_stack
        assert False, "expected ValueError"
    except ValueError as e:
        assert "not linear" in str(e)


def test_patch_stack_linear_despite_unrelated_diamond():
    from bugbug.tools.core.platforms.phabricator import PhabricatorPatch

    # PHID-E has a linear ancestry (E→C→A), but D creates a diamond among
    # unrelated branches (D depends on both B and C). The old code would bail;
    # the fixed code must return the 3-entry linear chain without any error.
    class FakePatch(PhabricatorPatch):
        def __init__(self, revision_phid=None):
            pass

        @property
        def _revision_metadata(self):
            return {"phid": "PHID-E"}

        @property
        def stack_graph(self):
            return {
                "PHID-A": [],
                "PHID-B": ["PHID-A"],
                "PHID-C": ["PHID-A"],
                "PHID-D": ["PHID-B", "PHID-C"],  # diamond, not in E's ancestry
                "PHID-E": ["PHID-C"],
            }

        @property
        def patch_set(self):
            return PatchSet.from_string("")

    fake = FakePatch()
    stack = fake.patch_stack
    assert len(stack) == 3  # A, C, E


# ---------------------------------------------------------------------------
# find_comment_scope
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# _fetch_file
# ---------------------------------------------------------------------------


def make_client(*, at_revision=None, latest=None):
    client = MagicMock()
    client.get_file_at_revision = AsyncMock(
        return_value=at_revision,
        side_effect=None if at_revision is not None else Exception("not found"),
    )
    client.get_file = AsyncMock(return_value=latest)
    return client


def make_patch_obj(*, old_file=None, old_file_exc=None):
    patch = MagicMock()
    if old_file_exc is not None:
        patch.get_old_file = AsyncMock(side_effect=old_file_exc)
    else:
        patch.get_old_file = AsyncMock(return_value=old_file)
    return patch


def test_fetch_file_uses_phabricator_first():
    patch = make_patch_obj(old_file="phab content")
    client = make_client(at_revision="rev content", latest="latest content")
    result = asyncio.run(_fetch_file("f.txt", "abc123", client, patch))
    assert result == "phab content"
    client.get_file_at_revision.assert_not_called()
    client.get_file.assert_not_called()


def test_fetch_file_falls_back_to_revision_on_file_not_found():
    patch = make_patch_obj(old_file_exc=FileNotFoundError("not in patch"))
    client = make_client(at_revision="rev content", latest="latest content")
    result = asyncio.run(_fetch_file("f.txt", "abc123", client, patch))
    assert result == "rev content"
    client.get_file.assert_not_called()


def test_fetch_file_falls_back_to_revision_on_http_error():
    response = httpx.Response(500, request=httpx.Request("GET", "http://x"))
    patch = make_patch_obj(
        old_file_exc=httpx.HTTPStatusError(
            "err", request=response.request, response=response
        )
    )
    client = make_client(at_revision="rev content", latest="latest content")
    result = asyncio.run(_fetch_file("f.txt", "abc123", client, patch))
    assert result == "rev content"
    client.get_file.assert_not_called()


def test_fetch_file_falls_back_to_latest_when_revision_fails():
    patch = make_patch_obj(old_file_exc=FileNotFoundError())
    client = make_client(latest="latest content")
    client.get_file_at_revision = AsyncMock(side_effect=Exception("searchfox error"))
    result = asyncio.run(_fetch_file("f.txt", "abc123", client, patch))
    assert result == "latest content"


def test_fetch_file_skips_revision_when_none():
    patch = make_patch_obj(old_file_exc=FileNotFoundError())
    client = make_client(latest="latest content")
    result = asyncio.run(_fetch_file("f.txt", None, client, patch))
    assert result == "latest content"
    client.get_file_at_revision.assert_not_called()
