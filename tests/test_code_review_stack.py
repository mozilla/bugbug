from types import SimpleNamespace

from bugbug.tools.code_review import stack as stack_module
from bugbug.tools.code_review.stack import (
    STACK_TOOLS_WARNING,
    PatchStack,
    StackEntry,
    format_stack_context,
    load_patch_stack,
)
from bugbug.tools.core.platforms.base import FileChange


def make_changes(*paths, added=1):
    return [FileChange(path, added, 1) for path in paths]


def make_stack_patch(display_id, title):
    return SimpleNamespace(
        patch_id=display_id, display_id=display_id, patch_title=title
    )


class BrokenStackPatch:
    patch_id = "99"
    stack_descendants: list = []

    @property
    def stack_ancestors(self):
        raise ValueError("Patch stack is not linear")


def test_load_patch_stack_reads_titles_and_changes(monkeypatch):
    monkeypatch.setattr(stack_module, "MAX_DETAILED_ANCESTORS", 1)
    requested = []

    def load_stack_file_changes(ancestors):
        requested.extend(ancestor.patch_id for ancestor in ancestors)
        return {"D2": make_changes("a.py")}

    patch = SimpleNamespace(
        patch_id="3",
        stack_ancestors=[
            make_stack_patch("D1", "Add `pattern`"),
            make_stack_patch("D2", "Add `matcher`"),
        ],
        stack_descendants=[make_stack_patch("D4", "Use `matcher`")],
        load_stack_file_changes=load_stack_file_changes,
    )

    stack = load_patch_stack(patch)

    assert requested == ["D2"], (
        "Only the nearest `MAX_DETAILED_ANCESTORS` should load their file changes."
    )
    assert stack.ancestors == [
        StackEntry("D1", "Add `pattern`"),
        StackEntry("D2", "Add `matcher`", make_changes("a.py")),
    ], "Ancestors should keep their file changes."
    assert stack.descendants == [StackEntry("D4", "Use `matcher`")], (
        "Descendants should be loaded by title only."
    )
    assert stack.errors == [], "A healthy stack should report no errors."


def test_load_patch_stack_records_errors():
    stack = load_patch_stack(BrokenStackPatch())

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
            StackEntry("D1", "Bottom", make_changes("bottom.py")),
            StackEntry("D2", "Add `matcher`", make_changes("python/path.py", added=3)),
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
    monkeypatch.setattr(stack_module, "MAX_LISTED_ANCESTORS", 2)
    monkeypatch.setattr(stack_module, "MAX_FILES_PER_ANCESTOR", 1)
    stack = PatchStack(
        ancestors=[
            StackEntry("D1", "Oldest", make_changes("oldest.py")),
            StackEntry("D2", "Middle"),
            StackEntry("D3", "Nearest", make_changes("first.py", "second.py")),
        ]
    )

    context = format_stack_context(stack)

    assert "first.py" in context, "The nearest ancestor should list its files."
    assert "… and 1 more files" in context, "Long file lists should be truncated."
    assert "- D2: Middle\n- … and 1 older" in context, (
        "Ancestors without file changes should be listed by title only."
    )
    assert "Oldest" not in context, (
        "Ancestors past `MAX_LISTED_ANCESTORS` should be left out."
    )
    assert "… and 1 older" in context, "The prompt should count omitted ancestors."
