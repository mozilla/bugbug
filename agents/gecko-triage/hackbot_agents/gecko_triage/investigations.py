"""The `bug_investigator` spawns of a run, which the needinfo check reads.

A bug with diagnostics needs a look at the code before the run asks the reporter for
more. The PostToolUse hook on the spawn tool counts each `bug_investigator` that ran, and
the `bugzilla.update_bug` hook refuses the first needinfo on the reporter when no spawn
came before it (see `early_ask_refusal`).
"""

from collections.abc import Collection
from dataclasses import dataclass

# The subagent name, which the main agent gives the spawn tool as `subagent_type`.
INVESTIGATOR = "bug_investigator"


@dataclass
class Investigations:
    """``spawns`` counts the `bug_investigator` spawns, ``refusals`` the refused asks."""

    spawns: int = 0
    refusals: int = 0


def note_spawn(store: Investigations, input_data: dict) -> None:
    """Count ``input_data``, a finished spawn, when it ran `bug_investigator`."""
    tool_input = input_data.get("tool_input") or {}
    if tool_input.get("subagent_type") == INVESTIGATOR:
        store.spawns += 1


def spawn_counter(store: Investigations):
    """The PostToolUse hook on the spawn tool: it counts a spawn after it ran."""

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        note_spawn(store, input_data)
        return {}

    return hook


def early_ask_refusal(
    store: Investigations, reporter: str, requestees: Collection[str]
) -> str | None:
    """Why a needinfo on ``reporter`` is refused, or None.

    Only the first needinfo on the reporter before any `bug_investigator` spawn is
    refused, so a bug with no data to analyze can still ask on the second attempt.
    """
    if store.spawns or store.refusals or not reporter or reporter not in requestees:
        return None
    store.refusals += 1
    return (
        f"you ask the reporter for data, but no {INVESTIGATOR} ran in this run. When "
        "the bug has diagnostics, such as memory maps, logs, profiles, crash data, a "
        f"regression range or measurements, spawn {INVESTIGATOR} first. Ask only for "
        "data that answers a question it names, and never for a fact the bug already "
        "shows. Record this change again unchanged only when the bug has no data to "
        "analyze"
    )
