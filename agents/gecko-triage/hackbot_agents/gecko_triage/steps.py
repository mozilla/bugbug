"""The steps of each session of a run, so that the step limits can come from data.

A PostToolUse and a PostToolUseFailure hook on every tool count the tool calls of each
session: the main agent, and each subagent spawn by its id. The hook on the spawn tool
also notes a subagent that stopped at its step limit, from the note that the CLI puts in
the spawn result. The run result records both (`findings.steps`, `findings.limit_hits`).
"""

import re
from dataclasses import dataclass, field

from .closing import response_texts

MAIN = "main"
# The CLI's note in a spawn result when the subagent stopped at its limit.
_LIMIT_NOTE = re.compile(r"stopped at its (\d+)-turn limit")


@dataclass
class Steps:
    """The tool calls of each session, and the spawns that stopped at their limit.

    ``calls`` maps a session key to its type and its tool calls. ``limit_hits`` lists
    the subagent type and the limit of each spawn that stopped at its limit.
    """

    calls: dict[str, list] = field(default_factory=dict)
    limit_hits: list[dict] = field(default_factory=list)

    def report(self) -> list[dict]:
        """One entry per session, in the order the sessions started."""
        return [{"session": kind, "tool_calls": n} for kind, n in self.calls.values()]


def note_call(store: Steps, input_data: dict) -> None:
    """Count one tool call of the session that made it."""
    agent_id = input_data.get("agent_id")
    kind = input_data.get("agent_type") if agent_id else MAIN
    key = agent_id or MAIN
    entry = store.calls.setdefault(key, [kind or "subagent", 0])
    entry[1] += 1


def note_limit_hit(store: Steps, input_data: dict) -> None:
    """Note a spawn whose result says that the subagent stopped at its limit."""
    for text in response_texts(input_data.get("tool_response")):
        match = _LIMIT_NOTE.search(text) if isinstance(text, str) else None
        if match:
            tool_input = input_data.get("tool_input") or {}
            store.limit_hits.append(
                {
                    "session": tool_input.get("subagent_type") or "subagent",
                    "limit": int(match[1]),
                }
            )
            return


def call_counter(store: Steps):
    """The PostToolUse and PostToolUseFailure hook on every tool."""

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        note_call(store, input_data)
        return {}

    return hook


def limit_hit_hook(store: Steps):
    """The PostToolUse hook on the spawn tool."""

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        note_limit_hit(store, input_data)
        return {}

    return hook
