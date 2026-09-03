# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Utilities for applying a patch stack to a base file."""

from typing import Awaitable, Callable, Optional

from unidiff import PatchedFile, PatchSet


def strip_diff_prefix(file_path: str) -> str:
    if file_path.startswith("a/") or file_path.startswith("b/"):
        return file_path[2:]
    return file_path


def find_patched_file(patch_set: PatchSet, file_path: str) -> Optional[PatchedFile]:
    """Return the PatchedFile for file_path in patch_set, or None."""
    for patched_file in patch_set:
        if file_path in {
            patched_file.path,
            strip_diff_prefix(patched_file.target_file),
        }:
            return patched_file
    return None


def apply_patched_file(base_content: str, patched_file: PatchedFile) -> str:
    """Apply a single patched file's hunks to base_content and return the result."""
    if patched_file.is_removed_file:
        raise FileNotFoundError("File is removed by the patch")

    base_lines = [] if patched_file.is_added_file else base_content.splitlines(True)
    new_lines = []
    source_index = 0

    # hunk.target_lines() only covers the hunk's span; we must explicitly copy
    # the unchanged base lines that fall between hunks ourselves.
    for hunk in patched_file:
        hunk_source_start = max(hunk.source_start - 1, 0)
        new_lines.extend(base_lines[source_index:hunk_source_start])
        new_lines.extend(line.value for line in hunk.target_lines())
        source_index = hunk_source_start + hunk.source_length

    new_lines.extend(base_lines[source_index:])
    return "".join(new_lines)


async def get_file_after_stack(
    patch_stack: list[PatchSet],
    file_path: str,
    fetch: Callable[[str], Awaitable[str]],
) -> str:
    """Return file_path after applying patch_stack on top of the base.

    fetch(path) is called to obtain the pre-stack base content. Rename chains
    are followed across the stack so the correct source file is fetched.

    Args:
        patch_stack: Ordered list of patch sets to apply.
        file_path: Repository-relative path to retrieve.
        fetch: Async callable that returns file content given a path.
    """
    normalized_path = strip_diff_prefix(file_path)

    # Walk the stack in reverse to collect patches that touch this file,
    # following rename chains to find the original source path.
    source_path = normalized_path
    patched_files = []
    for patch_set in reversed(patch_stack):
        pf = find_patched_file(patch_set, source_path)
        if pf is None:
            continue
        patched_files.append(pf)
        if not pf.is_added_file:
            source_path = strip_diff_prefix(pf.source_file)

    if not patched_files:
        return await fetch(normalized_path)

    patched_files.reverse()
    file_content = "" if patched_files[0].is_added_file else await fetch(source_path)
    for pf in patched_files:
        file_content = apply_patched_file(file_content, pf)
    return file_content
