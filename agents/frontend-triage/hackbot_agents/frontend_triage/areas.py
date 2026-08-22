"""The ``load_area_guidance`` tool -- read a `rules/areas/` file mid-run.

The prompt carries the guidance for the area the bug's component maps to. This is how
the agent gets a *different* one when its investigation says the code lives elsewhere,
and it records what was loaded so `hooks.area_guidance_hook` can tell whether a comment
is citing a tree the agent was told nothing about.

A plain ``Read`` of the file would work equally well for the agent and not at all for
the hook, which needs the load to be observable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from agent_tools.registry import ToolError, tool, tools_in
from pydantic import Field

from .config import AREAS, AREAS_BY_NAME


@dataclass
class AreaGuidanceContext:
    """Where the area files live, and which ones this run has loaded.

    ``loaded`` is shared with the hook rather than copied: it starts as the areas that
    were injected into the prompt, and each successful call adds to it.
    """

    areas_dir: Path
    loaded: set[str]


@tool
async def load_area_guidance(
    ctx: AreaGuidanceContext,
    area: Annotated[
        str,
        Field(
            description=(
                "Area name, exactly as listed in the system prompt's area index -- "
                "e.g. 'Windows installer', 'Site permissions'."
            )
        ),
    ],
) -> dict:
    """Read the source-tree guidance for one area.

    Call this when your investigation shows the bug's code is in an area whose guidance
    is not already in your prompt. Recording a comment that cites files from an
    unloaded area is refused.
    """
    entry = AREAS_BY_NAME.get(area) or next(
        (a for a in AREAS if a.name.casefold() == area.casefold()), None
    )
    if entry is None:
        raise ToolError(
            f"no area named {area!r}",
            payload={
                "error": "unknown_area",
                "requested": area,
                "known_areas": [a.name for a in AREAS],
            },
        )

    text = (ctx.areas_dir / f"{entry.slug}.md").read_text()
    ctx.loaded.add(entry.name)
    return {"area": entry.name, "guidance": text}


TOOLS = tools_in(__name__)
