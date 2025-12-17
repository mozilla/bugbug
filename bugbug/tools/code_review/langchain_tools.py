# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""LangGraph tools for code review agent."""

from dataclasses import dataclass

from langchain.tools import tool
from langgraph.runtime import get_runtime

from bugbug.code_search.function_search import FunctionSearch
from bugbug.tools.core.platforms.base import Patch


@dataclass
class CodeReviewContext:
    patch: Patch


@tool
def expand_context(file_path: str, start_line: int, end_line: int) -> str:
    """Show the content of a file between specified line numbers as it is before the patch.

    Be careful to not fill your context window with too much data. Request the
    minimum amount of context necessary to understand the code, but do not split
    what you really need into multiple requests if the line range is continuous.

    Args:
        file_path: The path to the file.
        start_line: The starting line number in the original file. Minimum is 1.
        end_line: The ending line number in the original file. Maximum is the total number of lines in the file.

    Returns:
        The content of the file between the specified line numbers.
    """
    runtime = get_runtime(CodeReviewContext)

    try:
        file_content = runtime.context.patch.get_old_file(file_path)
    except FileNotFoundError:
        return "File not found in the repository before the patch."

    lines = file_content.splitlines()
    start = max(1, start_line) - 1
    end = min(len(lines), end_line)

    # Format the output with line numbers that match the original file.
    line_number_width = len(str(end))
    return "\n".join(
        f"{i + 1:>{line_number_width}}| {lines[i]}" for i in range(start, end)
    )


def create_find_function_definition_tool(function_search: FunctionSearch):
    @tool
    def find_function_definition(
        file_path: str, line_number: int, function_name: str
    ) -> str:
        """Find the definition of a function based on its usage.

        Args:
            file_path: The path to the file where the function is used.
            line_number: The line number where the function is used. It should be based on the original file, not the patch.
            function_name: The name of the function to find its definition.

        Returns:
            The function definition.
        """
        functions = function_search.get_function_by_name(
            # TODO: We may want to use the patch base commit hash here instead of "tip".
            "tip",
            file_path,
            function_name,
        )

        if not functions:
            return "Function definition not found."

        return functions[0].source

    return find_function_definition
