"""Shared Codex SDK helpers for hackbot agents.

Generic, agent-neutral building blocks that every Codex SDK agent would
otherwise copy verbatim. Agents still start their own ``AsyncCodex`` threads
and read each turn's event stream — these just remove the boilerplate of
rendering the streamed notifications and collecting the turn outcome.

Requires the ``codex-sdk`` optional extra of hackbot-runtime.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from openai_codex import AsyncThread
from openai_codex.generated.v2_all import (
    AgentMessageThreadItem,
    CollabAgentToolCallThreadItem,
    CommandExecutionThreadItem,
    ContextCompactionThreadItem,
    DynamicToolCallThreadItem,
    ErrorNotification,
    FileChangeThreadItem,
    InputTextDynamicToolCallOutputContentItem,
    ItemCompletedNotification,
    ItemStartedNotification,
    McpToolCallThreadItem,
    MessagePhase,
    PlanThreadItem,
    ReasoningThreadItem,
    ThreadTokenUsage,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
    TurnStartedNotification,
    TurnStatus,
    UserMessageThreadItem,
    WebSearchThreadItem,
)
from openai_codex.models import UnknownNotification

from hackbot_runtime.transcript import TranscriptLog, _truncate


@dataclass
class CodexTurnResult:
    status: TurnStatus
    result: str | None
    error: str | None
    num_turns: int
    usage: ThreadTokenUsage | None
    duration_ms: int | None

    @property
    def is_error(self) -> bool:
        return self.status != TurnStatus.completed


class Reporter(TranscriptLog):
    """Render Codex events into readable logs."""

    def __init__(self, verbose: bool, log_path: Path | None):
        super().__init__(verbose, log_path)
        self._requests = 0
        self._tokens = {"input": 0, "cached": 0, "output": 0, "reasoning": 0}
        # the thread's token total from the last usage update, kept across turns
        self._last_total = None
        self._answer = None
        self._last_message = None
        self._usage = None
        self.result: CodexTurnResult | None = None

    async def session(self, thread: AsyncThread) -> None:
        info = (await thread.read()).thread
        self._emit(f"[system] session started (model={info.model or '?'})")

    def message(self, event) -> None:
        payload = event.payload

        if isinstance(payload, TurnStartedNotification):
            self._requests = 0
            self._tokens = {"input": 0, "cached": 0, "output": 0, "reasoning": 0}
            self._answer = None
            self._last_message = None
            self._usage = None
            self.result = None

        elif isinstance(payload, ThreadTokenUsageUpdatedNotification):
            self._usage = payload.token_usage

            # Counting a model request when total tokens increase and "last" has
            # input tokens, skipping repeated and context-full updates.
            total = payload.token_usage.total.total_tokens
            last = payload.token_usage.last
            rising = self._last_total is None or total > self._last_total
            if rising and last.input_tokens > 0:
                self._requests += 1
                self._tokens["input"] += last.input_tokens
                self._tokens["cached"] += last.cached_input_tokens
                self._tokens["output"] += last.output_tokens
                self._tokens["reasoning"] += last.reasoning_output_tokens
            self._last_total = total

        elif isinstance(payload, ItemStartedNotification):
            item = payload.item.root

            if isinstance(
                item,
                (
                    AgentMessageThreadItem,
                    CollabAgentToolCallThreadItem,
                    CommandExecutionThreadItem,
                    DynamicToolCallThreadItem,
                    FileChangeThreadItem,
                    McpToolCallThreadItem,
                    WebSearchThreadItem,
                ),
            ):
                self._turn += 1
                self._emit(f"\n--- turn {self._turn} ---")
            if isinstance(item, CommandExecutionThreadItem):
                arguments = {"command": item.command, "cwd": item.cwd.root}
                self._emit(
                    f"[agent→tool] shell({_truncate(json.dumps(arguments), 300)})",
                    full=f"[agent→tool] shell\n{json.dumps(arguments, indent=2)}",
                )
            elif isinstance(item, McpToolCallThreadItem):
                name = f"{item.server}.{item.tool}"
                arguments = json.dumps(item.arguments, default=str)

                self._emit(
                    f"[agent→tool] {name}({_truncate(arguments, 300)})",
                    full=(
                        f"[agent→tool] {name}\n"
                        f"{json.dumps(item.arguments, indent=2, default=str)}"
                    ),
                )
            elif isinstance(item, DynamicToolCallThreadItem):
                name = item.tool
                if item.namespace:
                    name = f"{item.namespace}.{item.tool}"

                arguments = json.dumps(item.arguments, default=str)

                self._emit(
                    f"[agent→tool] {name}({_truncate(arguments, 300)})",
                    full=(
                        f"[agent→tool] {name}\n"
                        f"{json.dumps(item.arguments, indent=2, default=str)}"
                    ),
                )

        elif isinstance(payload, ItemCompletedNotification):
            item = payload.item.root

            if isinstance(item, AgentMessageThreadItem):
                if item.phase == MessagePhase.final_answer:
                    self._answer = item.text
                elif item.phase is None:
                    self._last_message = item.text
                self._emit(f"[agent] {item.text}", always=True)
            elif isinstance(item, ReasoningThreadItem):
                thinking = "\n".join(
                    [*(item.summary or []), *(item.content or [])]
                ).strip()

                if thinking:
                    snippet = thinking.split("\n", 1)[0]
                    self._emit(
                        f"[agent:thinking] {_truncate(snippet, 120)}",
                        full=f"[agent:thinking]\n{thinking}",
                    )
            elif isinstance(item, CommandExecutionThreadItem):
                marker = "ok"
                if item.status.value in ("failed", "declined"):
                    marker = "ERROR"
                if item.exit_code not in (None, 0):
                    marker = "ERROR"

                output = item.aggregated_output or ""
                self._emit(
                    f"[tool←{marker}] {_truncate(output, 400)}",
                    full=f"[tool←{marker}]\n{output}",
                )
            elif isinstance(item, FileChangeThreadItem):
                paths = [change.path for change in item.changes]
                diffs = "\n".join(change.diff for change in item.changes)

                self._emit(
                    f"[agent→tool] apply_patch({_truncate(json.dumps(paths), 300)})",
                    full=f"[agent→tool] apply_patch\n{diffs}",
                )
                marker = "ok"
                if item.status.value in ("failed", "declined"):
                    marker = "ERROR"

                self._emit(f"[tool←{marker}] {item.status.value}")
            elif isinstance(item, McpToolCallThreadItem):
                marker = "ok"
                text = ""

                if item.error is not None:
                    marker = "ERROR"
                    text = item.error.message

                elif item.result is not None:
                    parts = []
                    for c in item.result.content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append(c.get("text", ""))
                    if parts:
                        text = "\n".join(parts)
                    elif item.result.structured_content is not None:
                        text = json.dumps(
                            item.result.structured_content,
                            default=str,
                        )

                if item.status.value == "failed":
                    marker = "ERROR"

                self._emit(
                    f"[tool←{marker}] {_truncate(text, 400)}",
                    full=f"[tool←{marker}]\n{text}",
                )
            elif isinstance(item, PlanThreadItem):
                self._emit(
                    f"[agent:plan] {_truncate(item.text, 300)}",
                    full=f"[agent:plan]\n{item.text}",
                )
            elif isinstance(item, WebSearchThreadItem):
                arguments = {"query": item.query}

                self._emit(
                    f"[agent→tool] web_search({_truncate(json.dumps(arguments), 300)})",
                    full=(
                        f"[agent→tool] web_search\n{json.dumps(arguments, indent=2)}"
                    ),
                )
            elif isinstance(item, CollabAgentToolCallThreadItem):
                name = f"collab.{item.tool.value}"
                arguments = {
                    "prompt": item.prompt,
                    "model": item.model,
                }

                self._emit(
                    f"[agent→tool] {name}({_truncate(json.dumps(arguments), 300)})",
                    full=(f"[agent→tool] {name}\n{json.dumps(arguments, indent=2)}"),
                )
            elif isinstance(item, ContextCompactionThreadItem):
                self._emit("[system:compaction] context compacted")
            elif isinstance(item, DynamicToolCallThreadItem):
                marker = "ok"
                if item.status.value == "failed" or item.success is False:
                    marker = "ERROR"

                parts = []
                for content in item.content_items or []:
                    if isinstance(
                        content.root,
                        InputTextDynamicToolCallOutputContentItem,
                    ):
                        parts.append(content.root.text)

                text = "\n".join(parts)

                self._emit(
                    f"[tool←{marker}] {_truncate(text, 400)}",
                    full=f"[tool←{marker}]\n{text}",
                )
            elif not isinstance(item, UserMessageThreadItem):
                data = json.dumps(
                    item.model_dump(mode="json", by_alias=True, warnings=False),
                    default=str,
                )

                self._emit(
                    f"[system:{item.type}] {_truncate(data, 200)}",
                    full=f"[system:{item.type}] {data}",
                )

        elif isinstance(payload, ErrorNotification):
            retry = " (retrying)" if payload.will_retry else ""

            self._emit(f"[system:error] {payload.error.message}{retry}")

        elif isinstance(payload, TurnCompletedNotification):
            turn = payload.turn

            error = None
            if turn.status != TurnStatus.completed:
                error = (
                    turn.error.message
                    if turn.error is not None
                    else f"turn {turn.status.value}"
                )

            self.result = CodexTurnResult(
                status=turn.status,
                result=self._answer if self._answer is not None else self._last_message,
                error=error,
                num_turns=self._requests,
                usage=self._usage,
                duration_ms=turn.duration_ms,
            )

            self._emit(f"\n{'=' * 60}", always=True, stamp=False)
            line = f"[done] turns={self._requests}"
            if turn.status != TurnStatus.completed:
                line += f" status={turn.status.value}"
            # explicit cost is not available, just token usage
            if self._requests:
                line += f" tokens=in:{self._tokens['input']}"
                line += f"(cached:{self._tokens['cached']})"
                line += f",out:{self._tokens['output']}"
                line += f"(reasoning:{self._tokens['reasoning']})"

            if turn.duration_ms is not None:
                line += f" elapsed={turn.duration_ms / 1000:.0f}s"
            self._emit(line, always=True)

            if error is not None:
                self._emit(f"[done] ERROR: {error}", always=True)

        # fallback for any other notification
        else:
            # skip incremental updates whose final state comes in an
            # item/completed or turn/completed notification anyway
            if event.method.lower().endswith("delta") or event.method in (
                "item/fileChange/patchUpdated",
                "item/reasoning/summaryPartAdded",
                "turn/diff/updated",
            ):
                return

            if isinstance(payload, UnknownNotification):
                data = payload.params
            else:
                data = payload.model_dump(mode="json", by_alias=True, warnings=False)
            text = json.dumps(data, default=str)

            self._emit(
                f"[system:{event.method}] {_truncate(text, 200)}",
                full=f"[system:{event.method}] {text}",
            )
