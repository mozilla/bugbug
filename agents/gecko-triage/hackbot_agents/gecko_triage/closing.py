"""The independent check of a closing decision.

Before the main agent closes a bug, the `close_verifier` subagent reads the bug with a
fresh context and tries to prove the close wrong. It records its verdict with
`record_close_verdict`, an in-process MCP server bound to this run's `CloseVerdicts`.
The record hooks read that store and refuse RESOLVED without a matching confirm that
came before the run's comment. The main agent may spawn the verifier once per run.
"""

from dataclasses import dataclass, field, replace
from typing import Annotated

from agent_tools.claude_sdk import build_sdk_server
from agent_tools.registry import ToolDefinition, ToolError
from pydantic import Field

from .actions_view import is_bug_id, updates
from .config import TRIAGE_RESOLUTIONS

SERVER_NAME = "verdicts"
RECORD_CLOSE_VERDICT_TOOL = f"mcp__{SERVER_NAME}__record_close_verdict"
# The subagent name, which the CLI gives a hook as `agent_type`.
VERIFIER = "close_verifier"
VERDICTS = ("confirm", "refute")


@dataclass(frozen=True)
class CloseVerdict:
    resolution: str
    dupe_of: int | None
    verdict: str
    reasons: str
    corrections: tuple[str, ...]
    # Whether the verdict came before the run's comment, so the comment could use it.
    before_comment: bool = True


# A close: its resolution and, for a duplicate, its `dupe_of`.
Close = tuple[str, int | None]


@dataclass
class CloseVerdicts:
    """The verdicts `close_verifier` recorded in this run, oldest first.

    ``comment_recorded`` is set once the run's comment is recorded,
    ``verifier_spawns`` counts the spawns of `close_verifier`, ``late_spawns`` the
    spawns refused after the comment, ``refused`` holds the resolution and the
    `dupe_of` of each close the hooks refused, and ``verifier_spilled`` is set once a
    tool result of the verifier was saved to a file it cannot read.
    """

    verdicts: list[CloseVerdict] = field(default_factory=list)
    comment_recorded: bool = False
    verifier_spawns: int = 0
    late_spawns: int = 0
    refused: list[tuple[object, object]] = field(default_factory=list)
    verifier_spilled: bool = False

    def refuted(self) -> bool:
        return any(v.verdict == "refute" for v in self.verdicts)

    def confirms(self, close: Close, *, late_too: bool = False) -> bool:
        """Whether a confirm covers ``close``.

        Only a confirm before the run's comment counts, unless ``late_too``.
        """
        return any(
            v.verdict == "confirm"
            and (v.resolution, v.dupe_of) == close
            and (v.before_comment or late_too)
            for v in self.verdicts
        )

    def warning(self, close: Close | None) -> str | None:
        """Why the run's comment may say it closes a bug the run left open, or None.

        ``close`` is the close the run recorded. A refused close counts unless a retry
        recorded the same close, since then the bug closes as the comment says.
        """
        uncovered = [a for a in self.refused if not _covers(close, a)]
        if uncovered:
            return (
                f"the recorder refused {len(uncovered)} close(s) that the run did not "
                "record after all, so the comment "
                "may say the bug closes while it stays open"
            )
        if self.late_spawns or any(not v.before_comment for v in self.verdicts):
            return (
                f"the run asked {VERIFIER} after it recorded the comment, so the "
                "comment did not use its verdict"
            )
        return None

    def outcome(self, close: Close | None) -> str | None:
        """`refuted` after any refute, `confirmed` when a confirm covers ``close``.

        ``close`` is the close the run recorded, or None when it recorded none.
        """
        if self.refuted():
            return "refuted"
        if close is not None and self.confirms(close):
            return "confirmed"
        return None


def _same(attempt: object, recorded: object) -> bool:
    return str(attempt).strip().upper() == str(recorded).strip().upper()


def _covers(close: Close | None, attempt: tuple[object, object]) -> bool:
    """Whether the recorded ``close`` is the refused ``attempt``, retried.

    A part the attempt left out, such as a missing `dupe_of`, matches any value.
    """
    if close is None:
        return False
    return all(a is None or _same(a, c) for a, c in zip(attempt, close, strict=True))


def _close_name(resolution: str, dupe_of: int | None) -> str:
    return f"RESOLVED {resolution}" + (
        f" with dupe_of {dupe_of}" if dupe_of is not None else ""
    )


def close_verdict_refusal(
    store: CloseVerdicts, resolution: str, dupe_of: int | None
) -> str | None:
    """Why this run may not record the close, or None when a confirm covers it.

    A refute rules out every close in the run, so asking the verifier again cannot
    turn it into a confirm. A confirm counts only when it came before the comment,
    since the run records one comment and a refute could not change it.
    """
    if store.refuted():
        return (
            "close_verifier refuted closing this bug, so do not close it. Record "
            "Analyzed or Needs info as workflow.md says, and apply its corrections to "
            "the comment"
        )
    close = (resolution, dupe_of)
    if store.confirms(close):
        return None
    if store.confirms(close, late_too=True):
        return (
            f"close_verifier confirmed {_close_name(resolution, dupe_of)} after you "
            "recorded the comment, so the comment could not use its verdict. Leave "
            "the bug open in this run. Spawn close_verifier before you record the "
            "comment"
        )
    return (
        f"{_close_name(resolution, dupe_of)} needs a confirm from close_verifier. "
        "Spawn close_verifier before you record the comment, with the bug id, this "
        "resolution, your reasons and the draft comment. Record the close only after "
        "a confirm, with its corrections applied. After a refute, do not close: "
        "record Analyzed or Needs info as workflow.md says, and use its corrections"
    )


def recorded_close(actions: list[dict]) -> Close | None:
    """The close among ``actions``, the recorded actions of a run, or None."""
    for update in updates(actions):
        if update.changes.get("status") == "RESOLVED":
            return update.changes.get("resolution"), update.changes.get("dupe_of")
    return None


def parse_verdict(
    resolution: object,
    dupe_of: object,
    verdict: object,
    reasons: object,
    corrections: object,
) -> CloseVerdict:
    """The verdict the tool stores. Raises ``ValueError`` on a bad argument."""
    name = resolution.strip().upper() if isinstance(resolution, str) else None
    if name not in TRIAGE_RESOLUTIONS:
        raise ValueError(
            f"resolution must be one of {', '.join(sorted(TRIAGE_RESOLUTIONS))}"
        )
    if name == "DUPLICATE" and not is_bug_id(dupe_of):
        raise ValueError("a DUPLICATE verdict needs dupe_of, the target's bug id")
    if name != "DUPLICATE" and dupe_of is not None:
        raise ValueError("dupe_of goes only with DUPLICATE")
    kind = verdict.strip().lower() if isinstance(verdict, str) else None
    if kind not in VERDICTS:
        raise ValueError("verdict must be confirm or refute")
    if not isinstance(reasons, str) or not reasons.strip():
        raise ValueError("give the reasons for the verdict")
    if not isinstance(corrections, list) or not all(
        isinstance(c, str) for c in corrections
    ):
        raise ValueError("corrections must be a list of strings")
    return CloseVerdict(
        resolution=name,
        dupe_of=dupe_of,
        verdict=kind,
        reasons=reasons.strip(),
        corrections=tuple(c.strip() for c in corrections if c.strip()),
    )


async def record_close_verdict_tool(
    store: CloseVerdicts,
    resolution: Annotated[
        str, Field(description="The proposed resolution, such as INVALID.")
    ],
    verdict: Annotated[str, Field(description="`confirm` or `refute`.")],
    reasons: Annotated[
        str, Field(description="Why the close stands or is wrong, with the evidence.")
    ],
    dupe_of: Annotated[
        int | None,
        Field(description="The duplicate target's bug id, only for DUPLICATE."),
    ] = None,
    corrections: Annotated[
        list[str] | None,
        Field(
            description=(
                "Each claim in the draft comment that is wrong or misleading, and "
                "each point it misses. Empty when there is nothing to correct."
            )
        ),
    ] = None,
) -> dict:
    try:
        found = parse_verdict(resolution, dupe_of, verdict, reasons, corrections or [])
    except ValueError as e:
        raise ToolError(
            str(e), payload={"error": "invalid_verdict", "reason": str(e)}
        ) from None
    if found.verdict == "confirm" and store.verifier_spilled:
        reason = "a tool result was too large to read; refute instead"
        raise ToolError(reason, payload={"error": "invalid_verdict", "reason": reason})
    if store.comment_recorded:
        found = replace(found, before_comment=False)
    store.verdicts.append(found)
    return {
        "recorded": found.verdict,
        "close": _close_name(found.resolution, found.dupe_of),
        "corrections": list(found.corrections),
    }


RECORD_CLOSE_VERDICT = ToolDefinition(
    name="record_close_verdict",
    namespace=SERVER_NAME,
    description="""Record your verdict on the proposed close of the bug being triaged.

Call it once, at the end of your check. `confirm` lets the triage agent record the close;
`refute` stops every close in this run. Give the resolution and, for DUPLICATE, the
dupe_of bug you checked. List every correction to the draft comment.""",
    handler=record_close_verdict_tool,
)


def verdicts_server(store: CloseVerdicts):
    """The in-process MCP server with ``record_close_verdict``, bound to ``store``."""
    return build_sdk_server(SERVER_NAME, store, [RECORD_CLOSE_VERDICT])


# What the Claude Code CLI (2.1.294) returns in place of a tool result it saved to a
# file: the large MCP result notice, and the persisted-output block for a result over
# the per-result or per-message limit. A spill replaces the whole result, so only a
# result or content block that starts with a notice is one; bug text can quote them.
PERSISTED_OUTPUT = "<persisted-output>"
MCP_SPILL_PREFIX = "Error: result ("
MCP_SPILL_SAVED = "Output has been saved to "


def _is_spill_text(text: object) -> bool:
    if not isinstance(text, str):
        return False
    return text.startswith(PERSISTED_OUTPUT) or (
        text.startswith(MCP_SPILL_PREFIX) and MCP_SPILL_SAVED in text
    )


def response_texts(response: object) -> list[object]:
    """The text of each content block of a PostToolUse ``tool_response``."""
    if isinstance(response, dict):
        response = response.get("content")
    if isinstance(response, list):
        return [b.get("text") if isinstance(b, dict) else b for b in response]
    return [response]


def is_spill_notice(response: object) -> bool:
    return any(_is_spill_text(text) for text in response_texts(response))


def note_verifier_spill(store: CloseVerdicts, input_data: dict) -> None:
    """Set ``verifier_spilled`` when a `close_verifier` tool result was saved to a file."""
    if input_data.get("agent_type") == VERIFIER and is_spill_notice(
        input_data.get("tool_response")
    ):
        store.verifier_spilled = True


def spill_hook(store: CloseVerdicts):
    """The PostToolUse hook on the MCP tools that notes a verifier spill.

    The CLI returns a spill as a normal result, so PostToolUseFailure never carries one.
    """

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        note_verifier_spill(store, input_data)
        return {}

    return hook


def comment_marker_hook(store: CloseVerdicts):
    """The last `bugzilla.add_comment` hook: it marks the run's comment as recorded."""

    def hook(action: dict) -> None:
        store.comment_recorded = True

    return hook


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


# The CLI's tool that spawns a subagent; older CLIs name it Task.
SPAWN_TOOLS = "Agent|Task"


def spawn_refusal(store: CloseVerdicts, input_data: dict) -> str | None:
    """Why a subagent spawn is refused, or None; it counts each `close_verifier` spawn.

    One spawn per run: a verifier that ends with no verdict leaves the close refused,
    and the main agent cannot ask again until one confirms.
    """
    tool_input = input_data.get("tool_input") or {}
    if tool_input.get("subagent_type") != VERIFIER:
        return None
    if store.comment_recorded:
        store.late_spawns += 1
        return (
            f"{VERIFIER} must run before the comment, and the comment is already "
            "recorded. The bug stays open in this run: do not close it, and report "
            "Analyzed or Needs info as workflow.md says"
        )
    if store.verifier_spawns:
        return (
            f"{VERIFIER} runs once per run, and it already ran. Do not close the bug "
            "without its confirm: record Analyzed or Needs info as workflow.md says"
        )
    store.verifier_spawns += 1
    return None


def spawn_guard(store: CloseVerdicts):
    """The PreToolUse hook that allows one `close_verifier` spawn per run."""

    async def guard(input_data: dict, tool_use_id: str | None, context) -> dict:
        reason = spawn_refusal(store, input_data)
        return {} if reason is None else _deny(reason)

    return guard


def verdict_caller_refusal(input_data: dict) -> str | None:
    """Why a call to ``record_close_verdict`` is refused, or None for the verifier.

    The main agent sees every MCP tool, so the hook keeps it from confirming its own
    close.
    """
    if input_data.get("agent_type") == VERIFIER:
        return None
    return f"only the {VERIFIER} subagent records a close verdict; spawn it"


async def verdict_caller_guard(
    input_data: dict, tool_use_id: str | None, context
) -> dict:
    reason = verdict_caller_refusal(input_data)
    return {} if reason is None else _deny(reason)
