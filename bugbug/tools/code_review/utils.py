# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


import json
import re
from itertools import chain
from logging import getLogger
from typing import Iterable

from unidiff import Hunk, PatchedFile, PatchSet

from bugbug.tools.core.data_types import InlineComment
from bugbug.tools.core.exceptions import (
    FileNotInPatchError,
    HunkNotInPatchError,
    ModelResultError,
)

logger = getLogger(__name__)


def find_comment_scope(file: PatchedFile, line_number: int):
    hunks_based_on_added = (
        hunk
        for hunk in file
        if hunk.target_start <= line_number <= hunk.target_start + hunk.target_length
    )
    hunks_based_on_deleted = (
        hunk
        for hunk in file
        if hunk.source_start <= line_number <= hunk.source_start + hunk.source_length
    )

    try:
        hunk = next(chain(hunks_based_on_added, hunks_based_on_deleted))
    except StopIteration as e:
        raise HunkNotInPatchError("Line number not found in the patch") from e

    has_added_lines = any(line.is_added for line in hunk)
    has_deleted_lines = any(line.is_removed for line in hunk)

    if has_added_lines and has_deleted_lines:
        first_line, last_line = find_mixed_lines_range(hunk)
    elif has_added_lines:
        first_line, last_line = find_added_lines_range(hunk)
    else:
        first_line, last_line = find_removed_lines_range(hunk)

    return {
        "line_start": first_line,
        "line_end": last_line,
        "has_added_lines": has_added_lines,
    }


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
                    # means that we are adding lines is the first
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
    hunk_with_lines = ""
    for line in hunk:
        if line.is_added:
            hunk_with_lines += f"{line.target_line_no} + {line.value}"
        elif line.is_removed:
            hunk_with_lines += f"{line.source_line_no} - {line.value}"
        elif line.is_context:
            hunk_with_lines += f"{line.target_line_no}   {line.value}"

    return hunk_with_lines


def format_patch_set(patch_set):
    output = ""
    for patch in patch_set:
        for hunk in patch:
            output += f"Filename: {patch.target_file}\n"
            output += f"{get_hunk_with_associated_lines(hunk)}\n"

    return output


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


def parse_model_output(output: str) -> list[dict]:
    # TODO: This function should raise an exception when the output is not valid
    # JSON. The caller can then decide how to handle the error.
    # For now, we just log the error and return an empty list.

    json_opening_tag = output.find("```json")
    if json_opening_tag != -1:
        json_closing_tag = output.rfind("```")
        if json_closing_tag == -1:
            logger.error("No closing ``` found for JSON in model output: %s", output)
            return []
        output = output[json_opening_tag + len("```json") : json_closing_tag]

    try:
        comments = json.loads(output)
    except json.JSONDecodeError:
        logger.error("Failed to parse JSON from model output: %s", output)
        return []

    return comments


def generate_processed_output(output: str, patch: PatchSet) -> Iterable[InlineComment]:
    comments = parse_model_output(output)

    patched_files_map = {
        patched_file.target_file: patched_file for patched_file in patch
    }

    for comment in comments:
        file_path = comment["file"]
        if not file_path.startswith("b/") and not file_path.startswith("a/"):
            file_path = "b/" + file_path

        # FIXME: currently, we do not handle renamed files

        patched_file = patched_files_map.get(file_path)
        if patched_file is None:
            raise FileNotInPatchError(
                f"The file `{file_path}` is not part of the patch: {list(patched_files_map)}"
            )

        line_number = comment["code_line"]
        if not isinstance(line_number, int):
            raise ModelResultError("Line number must be an integer")

        scope = find_comment_scope(patched_file, line_number)

        yield InlineComment(
            filename=(
                patched_file.target_file[2:]
                if scope["has_added_lines"]
                else patched_file.source_file[2:]
            ),
            start_line=line_number,
            end_line=line_number,
            hunk_start_line=scope["line_start"],
            hunk_end_line=scope["line_end"],
            content=comment["comment"],
            on_removed_code=not scope["has_added_lines"],
            explanation=comment["explanation"],
            order=comment["order"],
        )


def convert_generated_comments_to_inline(
    comments, patch: PatchSet
) -> Iterable[InlineComment]:
    """Convert GeneratedReviewComment objects to InlineComment objects.

    Args:
        comments: List of GeneratedReviewComment objects.
        patch: The PatchSet to validate file paths against.

    Yields:
        InlineComment objects with proper scope information.
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
            raise FileNotInPatchError(
                f"The file `{file_path}` is not part of the patch: {list(patched_files_map)}"
            )

        line_number = comment.code_line
        if not isinstance(line_number, int):
            raise ModelResultError("Line number must be an integer")

        scope = find_comment_scope(patched_file, line_number)

        yield InlineComment(
            filename=(
                patched_file.target_file[2:]
                if scope["has_added_lines"]
                else patched_file.source_file[2:]
            ),
            start_line=line_number,
            end_line=line_number,
            hunk_start_line=scope["line_start"],
            hunk_end_line=scope["line_end"],
            content=comment.comment,
            on_removed_code=not scope["has_added_lines"],
            explanation=comment.explanation,
            order=comment.order,
        )
