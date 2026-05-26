# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""LangGraph tools for code review agent."""

from dataclasses import dataclass
from logging import getLogger
from typing import Optional

from langchain.tools import tool
from langgraph.runtime import get_runtime
from requests import HTTPError

from bugbug.code_search.function_search import FunctionSearch
from bugbug.tools.code_review.data_types import Skill, SkillLoadError
from bugbug.tools.core.platforms.base import Patch
from bugbug.tools.core.platforms.patch_apply import get_file_after_stack

logger = getLogger(__name__)


def _tool_error(message: str, *, fatal: bool = False) -> str:
    prefix = "Fatal" if fatal else "Warning"
    return f"{prefix}: {message}"


@dataclass
class CodeReviewContext:
    patch: Patch


@tool
async def expand_context(
    file_path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> str:
    """Retrieve the content of a file, optionally restricted to a line range.

    Omit start_line and end_line to get the full file. When specifying a range,
    be careful to not fill your context window with too much data — request the
    minimum necessary, but do not split a continuous range into multiple requests.

    Args:
        file_path: Repository-relative path, e.g. 'dom/media/webaudio/AudioNode.h'.
        start_line: Starting line number (1-based). Omit to start from the beginning.
        end_line: Ending line number (inclusive). Omit to read to the end of the file.

    Returns:
        The file content, with line numbers prefixed.
    """
    runtime = get_runtime(CodeReviewContext)
    patch = runtime.context.patch

    warning = None
    try:
        patch_stack = patch.patch_stack
    except Exception as e:
        warning = f"Could not retrieve the full patch stack ({e}). File content reflects only this patch; please flag this in your review."
        patch_stack = [patch.patch_set]

    try:
        file_content = await get_file_after_stack(patch_stack, file_path, patch.get_old_file)
    except FileNotFoundError:
        return f"Warning: {file_path} was removed by the patch stack."
    except Exception as e:
        return f"Warning: could not retrieve {file_path}: {e}."

    lines = file_content.splitlines()
    start = max(1, start_line) - 1 if start_line is not None else 0
    end = min(len(lines), end_line) if end_line is not None else len(lines)

    line_number_width = len(str(end))
    content = "\n".join(
        f"{i + 1:>{line_number_width}}| {lines[i]}" for i in range(start, end)
    )
    if warning:
        return f"Warning: {warning}\n\n{content}"
    return content


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
        try:
            functions = function_search.get_function_by_name(
                # TODO: We may want to use the patch base commit hash here instead of "tip".
                "tip",
                file_path,
                function_name,
            )
        except HTTPError as e:
            logger.error(
                "HTTP error occurred while searching for the definition of function '%s' which is used in file '%s' at line %d: %s",
                function_name,
                file_path,
                line_number,
                e,
            )
            return "Error occurred while searching for the function definition."

        if not functions:
            return "Function definition not found."

        return functions[0].source

    return find_function_definition


def create_load_skill_tool(skills: list[Skill]):
    skills_by_name = {skill.name: skill for skill in skills}
    available_names = ", ".join(skills_by_name)

    catalog_lines = "\n".join(
        f"- **{skill.name}**: {skill.description}" for skill in skills
    )
    description = (
        "Load the contents of a named skill to guide the review. Use this when "
        "the patch touches an area covered by one of the skills below; otherwise, "
        "do not call it.\n\n"
        "Available skills:\n"
        f"{catalog_lines}\n\n"
        "Args:\n"
        "    name: The name of the skill to load (must match one of the names above).\n\n"
        "Returns:\n"
        "    The skill content as Markdown."
    )

    @tool(description=description)
    async def load_skill(name: str) -> str:
        skill = skills_by_name.get(name)
        if skill is None:
            return f"Unknown skill '{name}'. Available: {available_names}."

        try:
            return await skill.load()
        except SkillLoadError:
            logger.exception("Failed to load skill '%s'", name)
            return f"Failed to load skill '{name}'. Please proceed without it."

    return load_skill
