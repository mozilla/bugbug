import asyncio
import logging
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from unidiff import PatchSet

from bugbug.tools.code_review import data_types, langchain_tools, review_context
from bugbug.tools.code_review.data_types import ExternalContent, Skill, _strip_frontmatter
from bugbug.tools.code_review.langchain_tools import _fetch_file, create_load_skill_tool
from bugbug.tools.code_review.review_context import (
    _merge_rules,
    external_content_manifest,
    format_external_content,
    github_repo_allowed,
    load_external_content_for_diff,
    parse_diff_files,
    rule_matches,
)
from bugbug.tools.code_review.review_context_schema import (
    AnyFilePredicate,
    FilePredicate,
    ReviewContextValidationError,
    Rule,
    parse_review_context_toml,
)
from bugbug.tools.code_review.review_context_schema import (
    main as validate_review_context_main,
)
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


@pytest.mark.asyncio
async def test_external_content_load_caches():
    item = ExternalContent(
        name="a",
        url="https://example.com/a.md",
        description="example",
    )
    client = _mock_client_returning("---\nname: a\n---\nbody\n")
    with patch.object(data_types, "get_http_client", return_value=client):
        first = await item.load()
        second = await item.load()
    assert first == "body\n"
    assert second == "body\n"
    assert client.get.await_count == 1


_RULES_TOML = """
version = 1

[[rules]]
name = "Audio/Video C++"
when = { any_file = { include = ["dom/media/**"], ext = [".cpp", ".h"] } }
load = [
  { type = "file", path = ".claude/skills/dom-media.md" },
]

[[rules]]
name = "WebIDL"
when = { any_file = { ext = [".webidl"] } }
load = [
  { type = "file", path = ".claude/skills/webidl.md", repo = "mozilla-firefox/firefox" },
]

[[rules]]
name = "Any JS"
when = { any_file = { ext = [".js"] } }
load = [
  { type = "file", path = ".claude/skills/js-style.md" },
]

[[rules]]
name = "Bugzilla component only"
when = { bugzilla = { component = ["Core::DOM: Web Audio"] } }
load = [
  { type = "file", path = ".claude/skills/dom-audio.md" },
]
"""

_DIFF_MEDIA = """\
diff --git a/dom/media/Foo.cpp b/dom/media/Foo.cpp
--- a/dom/media/Foo.cpp
+++ b/dom/media/Foo.cpp
@@ -1,1 +1,1 @@
-old
+new
"""

_DIFF_WEBIDL = """\
diff --git a/dom/webidl/Foo.webidl b/dom/webidl/Foo.webidl
--- a/dom/webidl/Foo.webidl
+++ b/dom/webidl/Foo.webidl
@@ -1,1 +1,1 @@
-old
+new
"""


@pytest.fixture(autouse=True)
def clear_review_context_cache():
    review_context._review_context_cache.clear()
    yield
    review_context._review_context_cache.clear()


def test_parse_diff_files():
    files = parse_diff_files(_DIFF_MEDIA)
    assert files == {"dom/media/Foo.cpp"}


def test_github_repo_allowed_policy():
    assert github_repo_allowed("mozilla/cubeb", "example/repo", {"mozilla/"})
    assert github_repo_allowed("whatwg/html", "example/repo", {"whatwg/html"})
    assert github_repo_allowed("example/repo", "example/repo", set())
    assert not github_repo_allowed("mozilla/cubeb", "example/repo", set())
    assert not github_repo_allowed("whatwg/html-tests", "example/repo", {"whatwg/html"})


def test_rule_matches_extension_and_path():
    rule = Rule(
        name="test",
        when=AnyFilePredicate(FilePredicate(include=["dom/media/**"], ext=[".cpp"])),
        load=[],
    )
    assert rule_matches(rule, {"dom/media/Foo.cpp"})
    assert not rule_matches(rule, {"dom/canvas/Foo.cpp"})
    assert not rule_matches(rule, {"dom/media/Foo.js"})


def test_rule_matches_extension_only():
    rule = Rule(
        name="test",
        when=AnyFilePredicate(FilePredicate(ext=[".webidl"])),
        load=[],
    )
    assert rule_matches(rule, {"dom/webidl/Foo.webidl"})
    assert not rule_matches(rule, {"dom/webidl/Foo.js"})


def test_parse_review_context_toml_rejects_missing_when():
    toml = """
version = 1
[[rules]]
name = "Broken"
load = [{ type = "file", path = "x.md" }]
"""
    with pytest.raises(ReviewContextValidationError, match="rules\\[0\\].when"):
        parse_review_context_toml(toml)


def test_parse_review_context_toml_rejects_unknown_action_type():
    toml = """
version = 1
[[rules]]
name = "Broken"
when = { any_file = { ext = [".cpp"] } }
load = [{ type = "url", path = "x.md" }]
"""
    with pytest.raises(ReviewContextValidationError, match="unknown action type"):
        parse_review_context_toml(toml)


def test_parse_review_context_toml_rejects_unknown_rule_field():
    toml = """
version = 1
[[rules]]
name = "Broken"
bogus = true
when = { any_file = { ext = [".cpp"] } }
load = [{ type = "file", path = "x.md" }]
"""
    with pytest.raises(ReviewContextValidationError, match="unknown field"):
        parse_review_context_toml(toml)


def test_validate_review_context_main(tmp_path, capsys):
    review_context_path = tmp_path / "review-context.toml"
    review_context_path.write_text(_RULES_TOML)

    assert validate_review_context_main([str(review_context_path)]) == 0
    captured = capsys.readouterr()
    assert "valid" in captured.out


def test_validate_review_context_main_failure(tmp_path, capsys):
    review_context_path = tmp_path / "review-context.toml"
    review_context_path.write_text(
        """
version = 1
[[rules]]
name = "Broken"
load = [{ type = "file", path = "x.md" }]
"""
    )

    assert validate_review_context_main([str(review_context_path)]) == 1
    captured = capsys.readouterr()
    assert "invalid" in captured.err


@pytest.mark.asyncio
async def test_load_external_content_for_diff_file_load():
    review_context_repo = "mozilla-firefox/firefox"
    rules_response = MagicMock()
    rules_response.text = _RULES_TOML
    rules_response.raise_for_status = MagicMock()
    content_response = MagicMock()
    content_response.text = "---\nname: dom-media\n---\nMedia review guidelines\n"
    content_response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(side_effect=[rules_response, content_response])

    with patch.object(data_types, "get_http_client", return_value=client):
        results = await load_external_content_for_diff(
            _DIFF_MEDIA, review_context_repo, review_context_branch="release"
        )

    assert len(results) == 1
    assert results[0].name == ".claude/skills/dom-media.md"
    assert results[0].body == "Media review guidelines\n"
    assert results[0].source_type == "github_file"
    assert results[0].matched_rules == ["Audio/Video C++"]
    client.get.assert_any_await(
        "https://raw.githubusercontent.com/mozilla-firefox/firefox/refs/heads/release/review-context.toml",
        timeout=30,
    )
    client.get.assert_any_await(
        "https://raw.githubusercontent.com/mozilla-firefox/firefox/refs/heads/release/.claude/skills/dom-media.md",
        timeout=30,
    )


@pytest.mark.asyncio
async def test_load_external_content_for_diff_no_match():
    review_context_repo = "mozilla-firefox/firefox"
    rules_response = MagicMock()
    rules_response.text = _RULES_TOML
    rules_response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=rules_response)

    diff = """diff --git a/README.txt b/README.txt\n--- a/README.txt\n+++ b/README.txt\n@@ -1 +1 @@\n-a\n+b\n"""
    with patch.object(data_types, "get_http_client", return_value=client):
        results = await load_external_content_for_diff(diff, review_context_repo)

    assert results == []


@pytest.mark.asyncio
async def test_load_external_content_for_diff_rules_fetch_failure():
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))

    with patch.object(data_types, "get_http_client", return_value=client):
        results = await load_external_content_for_diff(
            _DIFF_MEDIA, "mozilla-firefox/firefox"
        )

    assert results == []


@pytest.mark.asyncio
async def test_load_external_content_deduplicates_actions():
    rules = """
version = 1

[[rules]]
name = "A"
when = { any_file = { ext = [".cpp"] } }
load = [{ type = "file", path = ".claude/skills/cpp.md" }]

[[rules]]
name = "B"
when = { any_file = { include = ["dom/media/**"] } }
load = [{ type = "file", path = ".claude/skills/cpp.md" }]
"""
    review_context_repo = "mozilla-firefox/firefox"
    rules_response = MagicMock()
    rules_response.text = rules
    rules_response.raise_for_status = MagicMock()
    content_response = MagicMock()
    content_response.text = "C++ guidelines"
    content_response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(side_effect=[rules_response, content_response])

    with patch.object(data_types, "get_http_client", return_value=client):
        results = await load_external_content_for_diff(
            _DIFF_MEDIA, review_context_repo
        )

    assert len(results) == 1
    assert results[0].matched_rules == ["A", "B"]
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_load_external_content_orders_by_priority():
    rules = """
version = 1

[[rules]]
name = "Low"
priority = 0
when = { any_file = { ext = [".cpp"] } }
load = [{ type = "file", path = ".claude/skills/low.md" }]

[[rules]]
name = "High"
priority = 10
when = { any_file = { ext = [".cpp"] } }
load = [{ type = "file", path = ".claude/skills/high.md" }]
"""
    review_context_repo = "mozilla-firefox/firefox"
    rules_response = MagicMock()
    rules_response.text = rules
    rules_response.raise_for_status = MagicMock()
    low_response = MagicMock()
    low_response.text = "low"
    low_response.raise_for_status = MagicMock()
    high_response = MagicMock()
    high_response.text = "high"
    high_response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(side_effect=[rules_response, high_response, low_response])

    with patch.object(data_types, "get_http_client", return_value=client):
        results = await load_external_content_for_diff(
            _DIFF_MEDIA,
            review_context_repo,
        )

    assert [r.name for r in results] == [
        ".claude/skills/high.md",
        ".claude/skills/low.md",
    ]


@pytest.mark.asyncio
async def test_load_external_content_rejects_disallowed_github_repo():
    rules = """
version = 1
[[rules]]
name = "Cross repo"
when = { any_file = { ext = [".webidl"] } }
load = [{ type = "file", repo = "mozilla/cubeb", path = "REVIEWING.md" }]
"""
    review_context_repo = "mozilla-firefox/firefox"
    rules_response = MagicMock()
    rules_response.text = rules
    rules_response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=rules_response)

    with patch.object(data_types, "get_http_client", return_value=client):
        results = await load_external_content_for_diff(
            _DIFF_WEBIDL,
            review_context_repo,
        )

    assert results == []
    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_load_external_content_allows_policy_prefix_repo():
    rules = """
version = 1
[policy.github]
allowed_repos = ["mozilla/"]
[[rules]]
name = "Cross repo"
when = { any_file = { ext = [".webidl"] } }
load = [{ type = "file", repo = "mozilla/cubeb", path = "REVIEWING.md" }]
"""
    review_context_repo = "mozilla-firefox/firefox"
    rules_response = MagicMock()
    rules_response.text = rules
    rules_response.raise_for_status = MagicMock()
    content_response = MagicMock()
    content_response.text = "cubeb guidelines"
    content_response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(side_effect=[rules_response, content_response])

    with patch.object(data_types, "get_http_client", return_value=client):
        results = await load_external_content_for_diff(
            _DIFF_WEBIDL,
            review_context_repo,
        )

    assert len(results) == 1
    assert results[0].source.endswith("/mozilla/cubeb/refs/heads/main/REVIEWING.md")


def test_merge_rules_appends_new():
    base = parse_review_context_toml(
        """
version = 1
[[rules]]
name = "A"
when = { any_file = { ext = [".cpp"] } }
load = [{ type = "file", path = "a.md" }]
"""
    )
    merged = _merge_rules(
        base.rules,
        """
version = 1
[[rules]]
name = "B"
when = { any_file = { ext = [".js"] } }
load = [{ type = "file", path = "b.md" }]
""",
    )
    assert [rule.name for rule in merged] == ["A", "B"]


def test_merge_rules_replaces_by_name():
    base = parse_review_context_toml(
        """
version = 1
[[rules]]
name = "A"
when = { any_file = { ext = [".cpp"] } }
load = [{ type = "file", path = "a.md" }]
"""
    )
    merged = _merge_rules(
        base.rules,
        """
version = 1
[[rules]]
name = "A"
when = { any_file = { ext = [".js"] } }
load = [{ type = "file", path = "replacement.md" }]
""",
    )
    assert len(merged) == 1
    assert merged[0].load[0].path == "replacement.md"


def test_merge_rules_empty_extra():
    base = parse_review_context_toml(
        """
version = 1
[[rules]]
name = "A"
when = { any_file = { ext = [".cpp"] } }
load = [{ type = "file", path = "a.md" }]
"""
    )
    merged = _merge_rules(base.rules, "version = 1\n")
    assert merged == base.rules


@pytest.mark.asyncio
async def test_content_override_used_instead_of_fetch():
    review_context_repo = "mozilla-firefox/firefox"
    rules_response = MagicMock()
    rules_response.text = _RULES_TOML
    rules_response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=rules_response)
    overrides = {".claude/skills/dom-media.md": "Override body"}

    with patch.object(data_types, "get_http_client", return_value=client):
        results = await load_external_content_for_diff(
            _DIFF_MEDIA, review_context_repo, content_overrides=overrides
        )

    assert len(results) == 1
    assert results[0].body == "Override body"
    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_external_content_manifest_and_prompt_body():
    review_context_repo = "mozilla-firefox/firefox"
    rules_response = MagicMock()
    rules_response.text = _RULES_TOML
    rules_response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=rules_response)
    overrides = {".claude/skills/dom-media.md": "Guideline body"}

    with patch.object(data_types, "get_http_client", return_value=client):
        results = await load_external_content_for_diff(
            _DIFF_MEDIA, review_context_repo, content_overrides=overrides
        )

    manifest = external_content_manifest(results)
    assert manifest == [
        {
            "name": ".claude/skills/dom-media.md",
            "source_type": "github_file",
            "source": "https://raw.githubusercontent.com/mozilla-firefox/firefox/refs/heads/main/.claude/skills/dom-media.md",
            "action": {"type": "file", "path": ".claude/skills/dom-media.md"},
            "matched_rules": ["Audio/Video C++"],
            "trusted": True,
            "trust_reason": "github_repo_content",
            "bytes": len("Guideline body".encode()),
            "sha256": results[0].sha256,
        }
    ]
    prompt = format_external_content(results)
    assert "<external_content_manifest>" in prompt
    assert "<external_context>" in prompt
    assert '<context name=".claude/skills/dom-media.md">' in prompt
    assert "Guideline body" in prompt
