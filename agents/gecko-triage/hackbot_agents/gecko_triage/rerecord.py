"""Let a run replace its own recorded comment and field changes.

The comment hook allows one comment per run, so a run that finds an error in its
recorded comment needs a way to record it again. `remove_recorded_actions`, an
in-process MCP server bound to the run's recorder, removes every recorded action: the
comment, every `bugzilla.update_bug` and the change on the `dupe_of` bug. The agent then
records them again, and every record hook runs again in order. The hooks read the
recorder for what is already recorded, so a re-record gets the same checks as a first
record.

The close verdict store keeps its state: the comment marker, the verdicts, the refused
closes and the spawn counts. So a verdict that came after the first comment stays late,
a refute still refuses every close and every closing comment, and a refused close still
warns unless the run records it again. A run may remove its actions `MAX_REMOVALS`
times. A run that removed them and ends with no comment fails, unless its decision needs
no comment. Only the main agent may call the tool.
"""

from dataclasses import dataclass

from agent_tools.claude_sdk import build_sdk_server
from agent_tools.registry import ToolDefinition, ToolError
from hackbot_runtime.actions import ActionsRecorder

SERVER_NAME = "rerecord"
REMOVE_RECORDED_ACTIONS_TOOL = f"mcp__{SERVER_NAME}__remove_recorded_actions"
MAX_REMOVALS = 2
# The decisions that need no comment; `skipped` comes from the preflight.
NO_COMMENT_DECISIONS = frozenset({"watching", "skipped"})


@dataclass
class Removals:
    """The run's recorder and the number of times the run removed its actions."""

    recorder: ActionsRecorder
    used: int = 0


async def remove_recorded_actions_tool(removals: Removals) -> dict:
    actions = removals.recorder.list_actions()
    if not actions:
        reason = "nothing is recorded yet, so there is nothing to remove"
        raise ToolError(reason, payload={"error": "nothing_recorded", "reason": reason})
    if removals.used >= MAX_REMOVALS:
        reason = (
            f"you removed the recorded actions {MAX_REMOVALS} times already; keep the "
            "actions as they are and name the error in your final message"
        )
        raise ToolError(reason, payload={"error": "limit_reached", "reason": reason})
    removals.used += 1
    for action in actions:
        removals.recorder.remove_action(action["action_id"])
    return {
        "removed": [a["type"] for a in actions],
        "removals_left": MAX_REMOVALS - removals.used,
        "next": (
            "Record the corrected comment first. Then record every field change again "
            "in one bugzilla_update_bug, and then the change on the dupe_of bug, if "
            "there was one."
        ),
    }


REMOVE_RECORDED_ACTIONS = ToolDefinition(
    name="remove_recorded_actions",
    namespace=SERVER_NAME,
    description=f"""Remove every action you recorded in this run, so you can record them again.

Use it when you find an error in your recorded comment. It removes the comment and every
field change, also the change on the dupe_of bug. Then record the corrected comment and
every field change again, in the same order; every check runs again. You can use it
{MAX_REMOVALS} times per run.""",
    handler=remove_recorded_actions_tool,
)


def missing_comment_refusal(
    removals: Removals, actions: list[dict], decision: str | None
) -> str | None:
    """Why a run that removed its actions fails: it did not record its comment again.

    The run then fails, so BugBot runs it again, instead of ending ok with no comment.
    """
    if not removals.used or decision in NO_COMMENT_DECISIONS:
        return None
    if any(a["type"] == "bugzilla.add_comment" for a in actions):
        return None
    return (
        "the run removed its recorded actions and did not record a comment again, "
        f"but the decision is {decision}"
    )


def removals_server(removals: Removals):
    """The in-process MCP server with ``remove_recorded_actions``, bound to ``removals``."""
    return build_sdk_server(SERVER_NAME, removals, [REMOVE_RECORDED_ACTIONS])


def removal_caller_refusal(input_data: dict) -> str | None:
    """Why a call to ``remove_recorded_actions`` is refused, or None for the main agent.

    The CLI gives a hook ``agent_id`` and ``agent_type`` only for a subagent.
    """
    if input_data.get("agent_id") or input_data.get("agent_type"):
        return "only the main agent removes its recorded actions"
    return None


async def removal_caller_guard(
    input_data: dict, tool_use_id: str | None, context
) -> dict:
    reason = removal_caller_refusal(input_data)
    if reason is None:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
