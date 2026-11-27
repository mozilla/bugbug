# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Describe the stack of patches that a reviewed patch belongs to."""

import asyncio
from dataclasses import dataclass, field
from logging import getLogger
from typing import Optional

from unidiff import PatchSet

from bugbug.tools.core.platforms.base import Patch

logger = getLogger(__name__)

# The nearest ancestors are listed with the files they touch, older ones by
# title only, to keep deep stacks within a reasonable prompt size.
MAX_DETAILED_ANCESTORS = 15
MAX_FILES_PER_ANCESTOR = 10
MAX_LISTED_ANCESTORS = 60
MAX_LISTED_DESCENDANTS = 15

STACK_TOOLS_WARNING = """\
The Searchfox-backed tools (`find_definition`, `search_text`, `search_identifier`,
`get_blame`, `get_function_at_line`, `get_field_layout`, `calls_from`, `calls_to`,
`calls_between` and `check_can_gc`) index mozilla-central, so they do not see this
patch or any patch in its stack. Before reporting that a symbol does not exist, that a
caller was not updated, or that code is unchanged, read the files the ancestors touch
with `expand_context`, which applies the whole stack.

Later patches may finish work that this patch starts. Do not report a missing
follow-up step that the title of a later patch says it covers."""


@dataclass(frozen=True)
class StackEntry:
    """One patch in the stack, other than the reviewed one."""

    display_id: str

    title: str

    # The changes of the patch, or `None` when they were not loaded.
    patch_set: Optional[PatchSet] = None


@dataclass(frozen=True)
class PatchStack:
    """The patches around the reviewed patch in its stack."""

    # The patches applied before the reviewed one, oldest first.
    ancestors: list[StackEntry] = field(default_factory=list)

    # The patches that build on the reviewed one, nearest first.
    descendants: list[StackEntry] = field(default_factory=list)

    # Problems that kept part of the stack from loading.
    errors: list[str] = field(default_factory=list)

    @property
    def is_stacked(self) -> bool:
        return bool(self.ancestors or self.descendants or self.errors)


async def load_patch_stack(patch: Patch) -> PatchStack:
    """Load the titles and changes of the patches around `patch`, concurrently.

    The stack is optional context, so a failure to load it is recorded in
    `errors` rather than raised.
    """
    (
        (ancestors, ancestors_error),
        (descendants, descendants_error),
    ) = await asyncio.gather(load_ancestors(patch), load_descendants(patch))
    errors = [error for error in (ancestors_error, descendants_error) if error]
    return PatchStack(ancestors, descendants, errors)


async def load_ancestors(patch: Patch) -> tuple[list[StackEntry], Optional[str]]:
    """Return the ancestors of `patch` with their changes, or the reason they failed."""
    logger.debug("Loading the ancestors of patch %s.", patch.patch_id)
    try:
        ancestors = await patch.get_stack_ancestors()
    except Exception as error:
        logger.warning("Could not load the ancestors of %s: %s", patch.patch_id, error)
        return [], f"The patches before this one could not be loaded ({error})."

    return [
        StackEntry(ancestor.display_id, ancestor.patch_title, ancestor.patch_set)
        for ancestor in ancestors
    ], None


async def load_descendants(patch: Patch) -> tuple[list[StackEntry], Optional[str]]:
    """Return the descendants of `patch` by title, or the reason they failed."""
    logger.debug("Loading the descendants of patch %s.", patch.patch_id)
    try:
        descendants = await patch.get_stack_descendants()
    except Exception as error:
        logger.warning(
            "Could not load the descendants of %s: %s", patch.patch_id, error
        )
        return [], f"The patches after this one could not be loaded ({error})."

    return [
        StackEntry(descendant.display_id, descendant.patch_title)
        for descendant in descendants
    ], None


def format_file_changes(patch_set: PatchSet) -> list[str]:
    """Return one line per file the patch touches, with its line counts."""
    lines = [
        f"  - {patched_file.path} (+{patched_file.added} -{patched_file.removed})"
        for patched_file in patch_set[:MAX_FILES_PER_ANCESTOR]
    ]
    if len(patch_set) > MAX_FILES_PER_ANCESTOR:
        lines.append(f"  - … and {len(patch_set) - MAX_FILES_PER_ANCESTOR} more files")
    return lines


def format_ancestors(ancestors: list[StackEntry]) -> list[str]:
    """List the ancestors nearest first, with the files the nearest ones touch."""
    nearest_first = list(reversed(ancestors))
    lines = [f"{len(ancestors)} patches come before this one, nearest first:"]
    for position, ancestor in enumerate(nearest_first[:MAX_LISTED_ANCESTORS]):
        lines.append(f"- {ancestor.display_id}: {ancestor.title}")
        if position < MAX_DETAILED_ANCESTORS and ancestor.patch_set is not None:
            lines.extend(format_file_changes(ancestor.patch_set))
    if len(nearest_first) > MAX_LISTED_ANCESTORS:
        lines.append(f"- … and {len(nearest_first) - MAX_LISTED_ANCESTORS} older")
    return lines


def format_descendants(descendants: list[StackEntry]) -> list[str]:
    """List the descendants nearest first, by title only."""
    lines = [f"{len(descendants)} patches build on this one, nearest first:"]
    lines.extend(
        f"- {descendant.display_id}: {descendant.title}"
        for descendant in descendants[:MAX_LISTED_DESCENDANTS]
    )
    if len(descendants) > MAX_LISTED_DESCENDANTS:
        lines.append(f"- … and {len(descendants) - MAX_LISTED_DESCENDANTS} more")
    return lines


def format_stack_context(stack: PatchStack) -> str:
    """Describe the stack for the review prompt, or return "" for a lone patch."""
    if not stack.is_stacked:
        return ""

    sections = [
        "This patch is part of a stack of patches. The patches before it have not "
        "landed, and this patch is written against the code as they leave it."
    ]
    if stack.ancestors:
        sections.append("\n".join(format_ancestors(stack.ancestors)))
    if stack.descendants:
        sections.append("\n".join(format_descendants(stack.descendants)))
    sections.extend(f"Warning: {error}" for error in stack.errors)
    sections.append(STACK_TOOLS_WARNING)

    return "\n\n".join(sections)
