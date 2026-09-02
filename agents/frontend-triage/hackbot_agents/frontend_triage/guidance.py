"""The ``load_component_guidance`` tool -- fetch another component's guidance mid-run.

How the agent gets guidance for a component other than the one the bug was filed in. A
plain ``Read`` would not serve it at all now that the guidance is in `config.py` rather
than on disk, and would not serve `hooks.component_guidance_hook` either, which needs the
load to be observable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from agent_tools.registry import ToolError, tool, tools_in
from pydantic import Field

from .config import TRIAGE_SCOPE
from .docs import DocRef, docs_for


@dataclass
class GuidanceContext:
    """The checkout to resolve docs against, and which components this run has loaded.

    ``loaded`` is shared with the hook rather than copied, and starts as whatever the
    prompt was built with, so a load here unblocks a comment the hook already refused.

    ``known_docs`` is the one `git grep` over the checkout, resolved by
    `run_frontend_triage` before the run starts. Required and never re-resolved here: a
    default would let a caller construct a context that silently searches again on every
    call, and on a checkout that legitimately has no docs that meant paying the
    subprocess and its 60s timeout each time.

    Note the two fields are mutable for different reasons. ``loaded`` is shared with the
    hook on purpose and mutating it is the point; ``known_docs`` is fixed for the run.
    """

    repo: Path
    loaded: set[str]
    known_docs: tuple[DocRef, ...]


@tool
async def load_component_guidance(
    ctx: GuidanceContext,
    product: Annotated[
        str,
        Field(description="Bugzilla product, e.g. 'Firefox', 'Firefox for Android'."),
    ],
    component: Annotated[
        str,
        Field(
            description=(
                "Bugzilla component, exactly as listed in the system prompt's component "
                "index -- e.g. 'Installer', 'Site Permissions'."
            )
        ),
    ],
) -> dict:
    """Read the triage guidance and source-docs pointers for one component.

    Call this when your investigation shows the bug's code belongs to a component whose
    guidance is not already in your prompt. Recording a comment that cites files owned by
    an unloaded component is refused.
    """
    key = f"{product.strip()} :: {component.strip()}"
    entry = next((c for c in TRIAGE_SCOPE if c.key.casefold() == key.casefold()), None)
    if entry is None:
        raise ToolError(
            f"no triaged component named {key!r}",
            payload={
                "error": "unknown_component",
                "requested": key,
                "known_components": [c.key for c in TRIAGE_SCOPE],
            },
        )

    ctx.loaded.add(entry.key)
    return {
        "component": entry.key,
        "trees": list(entry.trees),
        "docs": [
            {"tree": d.tree, "url": d.url} for d in docs_for(entry, ctx.known_docs)
        ],
        "notes": entry.notes,
    }


TOOLS = tools_in(__name__)
