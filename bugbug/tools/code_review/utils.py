# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


import re
from logging import getLogger
from typing import Iterable

from unidiff import Hunk, PatchedFile, PatchSet

from bugbug.tools.core.data_types import InlineComment
from bugbug.tools.core.exceptions import CommentNotLocatedError

logger = getLogger(__name__)


def _normalize_line(line: str) -> str:
    """Strip whitespace and an optional leading diff marker from a line."""
    line = line.strip()
    if line[:1] in ("+", "-"):
        line = line[1:]
    return line.strip()


def _side_lines(hunk: Hunk, new_side: bool) -> list[tuple[int, str]]:
    """Extract one side of a hunk as (line_number, normalized_content) pairs.

    new_side=True: context + added lines, numbered against the new file.
    new_side=False: context + removed lines, numbered against the old file.
    """
    result = []
    for line in hunk:
        if new_side:
            if line.is_context or line.is_added:
                result.append((line.target_line_no, _normalize_line(line.value)))
        elif line.is_context or line.is_removed:
            result.append((line.source_line_no, _normalize_line(line.value)))
    return result


def _match_consecutive(
    side_lines: list[tuple[int, str]], target_lines: list[str]
) -> tuple[int, int] | None:
    """Find a consecutive run in `side_lines` matching `target_lines`, if any."""
    n = len(target_lines)
    if n == 0 or len(side_lines) < n:
        return None
    for i in range(len(side_lines) - n + 1):
        if all(side_lines[i + j][1] == target_lines[j] for j in range(n)):
            return side_lines[i][0], side_lines[i + n - 1][0]
    return None


def find_comment_location(file: PatchedFile, existing_code: str) -> dict:
    """Locate the line range in `file` matching `existing_code` verbatim.

    Rather than trusting a model-provided line number (which requires
    counting from a hunk header and is error-prone), this matches the quoted
    snippet against the actual patch content and derives line numbers
    deterministically — the approach used by alibaba/open-code-review. Tries
    the new side (context + added lines) first, then the old side (context +
    removed lines), across all hunks.

    Raises CommentNotLocatedError if no hunk contains a run of lines matching
    `existing_code`.
    """
    target_lines = [
        _normalize_line(line) for line in existing_code.splitlines() if line.strip()
    ]
    if not target_lines:
        raise CommentNotLocatedError("existing_code is empty")

    for new_side in (True, False):
        for hunk in file:
            match = _match_consecutive(_side_lines(hunk, new_side), target_lines)
            if match is None:
                continue

            line_start, line_end = match
            has_added_lines = any(line.is_added for line in hunk)
            has_deleted_lines = any(line.is_removed for line in hunk)
            if has_added_lines and has_deleted_lines:
                hunk_start, hunk_end = find_mixed_lines_range(hunk)
            elif has_added_lines:
                hunk_start, hunk_end = find_added_lines_range(hunk)
            else:
                hunk_start, hunk_end = find_removed_lines_range(hunk)

            return {
                "line_start": line_start,
                "line_end": line_end,
                "hunk_start_line": hunk_start,
                "hunk_end_line": hunk_end,
                "has_added_lines": new_side,
            }

    raise CommentNotLocatedError(
        f"Could not find existing_code in the patch: {existing_code!r}"
    )


def find_line_text(file: PatchedFile, line_number: int) -> str:
    """Return the content of the line numbered `line_number` in `file`.

    Checks new-file numbering (context + added lines) first, then old-file
    numbering (context + removed lines). Used to backfill `existing_code` for
    few-shot examples sourced from historical (file, line_number) comments.
    """
    for hunk in file:
        for line in hunk:
            if line.target_line_no == line_number and not line.is_removed:
                return line.value.rstrip("\n")
    for hunk in file:
        for line in hunk:
            if line.source_line_no == line_number and not line.is_added:
                return line.value.rstrip("\n")
    raise CommentNotLocatedError(f"Line {line_number} not found in {file.path}")


def find_added_lines_range(hunk: Hunk):
    added_lines = [line.target_line_no for line in hunk if line.is_added]
    return added_lines[0], added_lines[-1]


def find_removed_lines_range(hunk: Hunk):
    removed_lines = [line.source_line_no for line in hunk if line.is_removed]
    return removed_lines[0], removed_lines[-1]


def find_mixed_lines_range(hunk: Hunk):
    def get_first_line(_hunk: Hunk, default: int | None = None):
        for i, line in enumerate(_hunk):
            if line.is_context:
                continue
            if line.target_line_no is None:
                if i == 0:
                    # If this is the first line of the hunk, it
                    # means that we are adding lines at the first
                    # line in the file.
                    return default
                return _hunk[i - 1].target_line_no
            return line.target_line_no

        # This should never happen
        raise ValueError("Cannot find the line number")

    first_line = get_first_line(hunk, 1)
    last_line = get_first_line(list(reversed(hunk)))
    if last_line is None:
        _, last_line = find_added_lines_range(hunk)

    return first_line, last_line


def get_hunk_with_associated_lines(hunk):
    lines = []
    for line in hunk:
        content = line.value.rstrip("\n")
        if line.is_added:
            lines.append(f"{line.target_line_no} + {content}")
        elif line.is_removed:
            lines.append(f"{line.source_line_no} - {content}")
        elif line.is_context:
            lines.append(f"{line.target_line_no}   {content}")

    return "\n".join(lines)


def format_patch_set(patch_set):
    """Render a PatchSet as a unified diff, with an added line-number column.

    The `---`/`+++`/`@@` headers match the unified diff format models are
    trained on. The added line-number column isn't part of standard diff
    syntax, but gives the model an easy way to double-check counting; comment
    placement itself is resolved from quoted `existing_code`, not this number.
    """
    output = []
    for patch in patch_set:
        old_path = (
            patch.source_file
            if patch.source_file != "/dev/null"
            else f"a/{patch.path}"
        )
        new_path = (
            patch.target_file
            if patch.target_file != "/dev/null"
            else f"b/{patch.path}"
        )
        output.append(f"diff --git {old_path} {new_path}")
        output.append(f"--- {patch.source_file}")
        output.append(f"+++ {patch.target_file}")
        for hunk in patch:
            output.append(
                f"@@ -{hunk.source_start},{hunk.source_length} "
                f"+{hunk.target_start},{hunk.target_length} @@"
            )
            output.append(get_hunk_with_associated_lines(hunk))

    return "\n".join(output) + "\n"


def get_associated_file_to_function(function_name, patch):
    for patch_by_file in patch:
        for one_patch in patch_by_file:
            if function_name in str(one_patch.source):
                return patch_by_file.path
    return None


def get_associated_file_to_line_context(context_line, patch):
    for key, value in patch.items():
        if context_line in str(value):
            return key
    return None


def parse_text_for_dict(text):
    file_content = {}
    current_filename = None
    current_lines = []

    lines = text.split("\n")
    for line in lines:
        if line.startswith("Filename:"):
            filename = line.split(":", 1)[1].strip()
            # Remove the first letter and the '/' character from the filename
            filename = filename[2:]
            current_filename = filename
            current_lines = []
        else:
            current_lines.append(line)

        # If we have content and filename, store it
        if current_filename is not None and len(current_lines) > 0:
            if file_content.get(current_filename) is not None:
                file_content[current_filename] = (
                    file_content[current_filename] + "\n" + str(line)
                )
            else:
                file_content[current_filename] = "\n".join(current_lines)

    return file_content


def len_common_path(f1, f2):
    """Find length of the common path."""
    f1_subsystems = f1.split("/")
    if f1 == f2:
        return len(f1_subsystems)

    f2_subsystems = f2.split("/")

    max_common_path_length = next(
        idx
        for idx, (sub1, sub2) in enumerate(zip(f1_subsystems, f2_subsystems))
        if sub1 != sub2
    )
    return max_common_path_length


def solve_conflict_definitions(target_path, functions):
    functions_common_path = [
        (len_common_path(target_path, fun.file), fun) for fun in functions
    ]
    max_common_path_length = max(
        [common_path_length for (common_path_length, _) in functions_common_path]
    )
    functions = [
        fun
        for (common_path_length, fun) in functions_common_path
        if common_path_length == max_common_path_length
    ]

    if len(functions) == 1:
        return functions
    else:
        return []  # could not solve conflict


def request_for_function_declarations(
    function_search, commit_hash, functions_list, patch_set
):
    functions_declarations = []

    if functions_list is not None:
        for function_name in functions_list:
            if (
                function_name != "Not found"
                and function_name != "N/A"
                and function_name != "None"
                and function_name != ""
                and len(function_name) < 50
            ):
                target_path = get_associated_file_to_line_context(
                    function_name, parse_text_for_dict(format_patch_set(patch_set))
                )

                if target_path:
                    definitions = function_search.get_function_by_name(
                        commit_hash,
                        path=target_path,
                        function_name=function_name,
                    )
                    if len(definitions) > 1:
                        definitions = solve_conflict_definitions(
                            target_path, definitions
                        )

                    collect_function_definitions(
                        functions_declarations, function_name, definitions
                    )

    return functions_declarations


def is_code_line_already_covered(code_line, target_file, function_declarations):
    for function_declaration in function_declarations:
        if (
            function_declaration[1] == target_file
            and code_line in function_declaration[2]
        ):
            return True
    return False


def collect_function_definitions(function_declarations, target_element, definitions):
    for definition in definitions:
        function_declarations.append(
            [
                target_element,
                definition.file,
                definition.source,
            ]
        )


def gather_line_context(line_context):
    r"""Reformat the line context list and remove duplicates.

    Args:
        line_context: List of lists, where each list is [line, file, function].

    Returns:
        List of tuples, where each tuple is (gathered_line, file, function). The
        'gathered_line' is a str that concatenates the 'line' with a separator
        (i.e., `\n`) that required the same function.
    """
    file_dir = {}

    for line, file, func in line_context:
        if file not in file_dir:
            file_dir[file] = {}
        if func not in file_dir[file]:
            file_dir[file][func] = []
        file_dir[file][func].append(line)

    gathered_context = []
    for file, funcs in file_dir.items():
        for func, lines in funcs.items():
            gathered_requested_lines = "\n".join(lines)
            gathered_context.append((gathered_requested_lines, file, func))
    return gathered_context


def request_for_context_lines(function_search, commit_hash, context_line_codes, patch):
    functions_declarations = []

    if context_line_codes is not None:
        for context_line in context_line_codes:
            try:
                line_number = int(re.search(r"\b(\d+)\b", context_line).group(1))
            except (AttributeError, ValueError):
                print("Unexpected Line Number Format")
                continue

            try:
                content_line = str(context_line.split(str(line_number))[1]).lstrip()[1:]
            except (IndexError, TypeError):
                print("Unexpected content line")
                continue

            target_path = get_associated_file_to_line_context(
                content_line, parse_text_for_dict(patch)
            )
            if (
                target_path
                and content_line
                and not is_code_line_already_covered(
                    content_line, target_path, functions_declarations
                )
            ):
                definitions = function_search.get_function_by_line(
                    commit_hash=commit_hash,
                    path=target_path,
                    line=line_number,
                )
                collect_function_definitions(
                    functions_declarations, context_line, definitions
                )

    functions_declarations = gather_line_context(functions_declarations)
    return functions_declarations


def get_structured_functions(target, functions_declaration):
    function_declaration_text = "\n"
    for function in functions_declaration:
        try:
            current_function_info = ""
            current_function_info += target + ": " + function[0] + "\n"
            current_function_info += "File: " + function[1] + "\n"
            if isinstance(function[2], list):
                current_function = ""
                for line in function[2]:
                    current_function += "\n" + line
                current_function_info += (
                    "Function Declaration: " + current_function + "\n\n"
                )
            else:
                current_function_info += (
                    "Function Declaration: \n" + function[2] + "\n\n"
                )
            function_declaration_text += current_function_info
        except IndexError:
            print("Function does not present all required information")
            continue

    return function_declaration_text


def convert_generated_comments_to_inline(
    comments, patch: PatchSet
) -> Iterable[InlineComment]:
    """Convert GeneratedReviewComment objects to InlineComment objects.

    Args:
        comments: List of GeneratedReviewComment objects.
        patch: The PatchSet to validate file paths against.

    Yields:
        InlineComment objects with proper scope information. Comments whose
        file or existing_code can't be resolved against the patch are
        skipped (and logged) rather than aborting the whole batch — one bad
        comment shouldn't cost the reviewee every other comment.
    """
    patched_files_map = {
        patched_file.target_file: patched_file for patched_file in patch
    }

    for comment in comments:
        file_path = comment.file
        if not file_path.startswith("b/") and not file_path.startswith("a/"):
            file_path = "b/" + file_path

        # FIXME: currently, we do not handle renamed files

        patched_file = patched_files_map.get(file_path)
        if patched_file is None:
            logger.warning(
                "Dropping comment: file `%s` is not part of the patch: %s",
                file_path,
                list(patched_files_map),
            )
            continue

        try:
            location = find_comment_location(patched_file, comment.existing_code)
        except CommentNotLocatedError:
            logger.warning(
                "Dropping comment: could not locate existing_code in `%s`: %r",
                file_path,
                comment.existing_code,
            )
            continue

        yield InlineComment(
            filename=(
                patched_file.target_file[2:]
                if location["has_added_lines"]
                else patched_file.source_file[2:]
            ),
            start_line=location["line_start"],
            end_line=location["line_end"],
            hunk_start_line=location["hunk_start_line"],
            hunk_end_line=location["hunk_end_line"],
            content=comment.comment,
            on_removed_code=not location["has_added_lines"],
            explanation=comment.explanation,
            order=comment.order,
        )
