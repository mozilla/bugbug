import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from unidiff import PatchSet

from bugbug.tools.code_review import stack as stack_module
from bugbug.tools.code_review.stack import (
    STACK_TOOLS_WARNING,
    PatchStack,
    StackEntry,
    format_stack_context,
    load_patch_stack,
)


def make_diff(path, added=1):
    added_lines = "".join(f"+line {number}\n" for number in range(added))
    return PatchSet.from_string(
        f"--- a/{path}\n+++ b/{path}\n@@ -1 +1,{added} @@\n-old\n{added_lines}"
    )


def make_stack_patch(display_id, title, patch_set=None):
    return SimpleNamespace(
        display_id=display_id, patch_title=title, patch_set=patch_set
    )


class BrokenStackPatch:
    patch_id = "99"

    async def get_stack_ancestors(self):
        raise ValueError("Patch stack is not linear")

    async def get_stack_descendants(self):
        return []


def test_load_patch_stack_reads_titles_and_changes():
    ancestor_diff = make_diff("a.py")
    patch = SimpleNamespace(
        patch_id="3",
        get_stack_ancestors=AsyncMock(
            return_value=[make_stack_patch("D1", "Add `matcher`", ancestor_diff)]
        ),
        get_stack_descendants=AsyncMock(
            return_value=[make_stack_patch("D4", "Use `matcher`")]
        ),
    )

    stack = asyncio.run(load_patch_stack(patch))

    assert stack.ancestors == [StackEntry("D1", "Add `matcher`", ancestor_diff)], (
        "Ancestors should keep their changes."
    )
    assert stack.descendants == [StackEntry("D4", "Use `matcher`")], (
        "Descendants should be loaded by title only."
    )
    assert stack.errors == [], "A healthy stack should report no errors."


def test_load_patch_stack_records_errors():
    stack = asyncio.run(load_patch_stack(BrokenStackPatch()))

    assert stack.ancestors == [], "Unreadable ancestors should be left out."
    assert stack.errors == [
        "The patches before this one could not be loaded (Patch stack is not linear)."
    ], "The reason the ancestors could not be loaded should be recorded."
    assert format_stack_context(stack).count("Warning:") == 1, (
        "The prompt should tell the model the stack is incomplete."
    )


def test_format_stack_context_is_empty_for_lone_patch():
    assert format_stack_context(PatchStack()) == "", (
        "A patch that is not in a stack should add nothing to the prompt."
    )


def test_format_stack_context_lists_ancestors_nearest_first():
    stack = PatchStack(
        ancestors=[
            StackEntry("D1", "Bottom", make_diff("bottom.py")),
            StackEntry("D2", "Add `matcher`", make_diff("python/path.py", added=3)),
        ],
        descendants=[StackEntry("D4", "Use `matcher` everywhere")],
    )

    context = format_stack_context(stack)

    assert context.index("D2: Add `matcher`") < context.index("D1: Bottom"), (
        "The nearest ancestor should be listed first."
    )
    assert "  - python/path.py (+3 -1)" in context, (
        "Each ancestor should list the files it touches with line counts."
    )
    assert "1 patches build on this one" in context, "Descendants should be listed."
    assert "D4: Use `matcher` everywhere" in context, (
        "Descendants should be listed by title."
    )
    assert context.endswith(STACK_TOOLS_WARNING), (
        "The prompt should warn that Searchfox does not see the stack."
    )


def test_format_stack_context_truncates_deep_stacks(monkeypatch):
    monkeypatch.setattr(stack_module, "MAX_DETAILED_ANCESTORS", 1)
    monkeypatch.setattr(stack_module, "MAX_LISTED_ANCESTORS", 2)
    monkeypatch.setattr(stack_module, "MAX_FILES_PER_ANCESTOR", 1)
    two_files = PatchSet.from_string(
        str(make_diff("first.py")) + str(make_diff("second.py"))
    )
    stack = PatchStack(
        ancestors=[
            StackEntry("D1", "Oldest", make_diff("oldest.py")),
            StackEntry("D2", "Middle", make_diff("middle.py")),
            StackEntry("D3", "Nearest", two_files),
        ]
    )

    context = format_stack_context(stack)

    assert "first.py" in context, "The nearest ancestor should list its files."
    assert "… and 1 more files" in context, "Long file lists should be truncated."
    assert "middle.py" not in context, (
        "Ancestors past `MAX_DETAILED_ANCESTORS` should be listed by title only."
    )
    assert "D2: Middle" in context, "Listed ancestors should keep their titles."
    assert "Oldest" not in context, (
        "Ancestors past `MAX_LISTED_ANCESTORS` should be left out."
    )
    assert "… and 1 older" in context, "The prompt should count omitted ancestors."
