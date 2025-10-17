# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""LangGraph tools for code review agent."""

from dataclasses import dataclass
from typing import Any

from langchain_core.tools import tool
from langgraph.runtime import get_runtime

from bugbug.code_search.function_search import FunctionSearch


@dataclass
class CodeReviewContext:
    # TODO: Move the Patch class out of `code_review` to avoid circular imports. Then use it here.
    patch: Any


@tool
def expand_context(file_path: str, line_number: int) -> str:
    """Expand the context around a specific line in a file diff.

    Args:
        file_path: The path to the file.
        line_number: The line number to expand context around. It should be based on the original file, not the patch.

    Returns:
        Lines of code around the specified line number.
    """
    runtime = get_runtime(CodeReviewContext)
    file_content = runtime.context.patch.get_old_file(file_path)

    # TODO: Expanding the context using an AST parser like tree-sitter to
    # include the whole function or class when it is relatively small.
    lines = file_content.splitlines()
    start = max(0, line_number - 20)
    end = min(len(lines), line_number + 20)

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
