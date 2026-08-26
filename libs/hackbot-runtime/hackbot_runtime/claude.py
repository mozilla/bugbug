"""Shared claude-agent-sdk helpers for hackbot agents.

Generic, agent-neutral building blocks that every claude-agent-sdk agent would
otherwise copy verbatim. Agents still assemble their own ``ClaudeAgentOptions``
and drive the ``ClaudeSDKClient`` loop — these just remove the boilerplate of
rendering the streamed messages.

Requires the ``claude-sdk`` optional extra of hackbot-runtime.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from claude_agent_sdk import (
    TERMINAL_TASK_STATUSES,
    AssistantMessage,
    ClaudeSDKClient,
    Message,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


class UnsettledResponseError(RuntimeError):
    """``receive_settled_response`` gave up before the agent's turn settled.

    ``pending`` is the ``task_id -> description`` map of deferring tasks
    still open when this was raised (empty if none were ever pending — the
    connection just ended with no result at all). Stored as an attribute
    for callers that want to inspect it programmatically, e.g. to name the
    stuck task(s) rather than just log the message.
    """

    def __init__(self, reason: str, pending: dict[str, str]):
        self.reason = reason
        self.pending = pending

    def __str__(self) -> str:
        if not self.pending:
            return self.reason
        return f"{self.reason} ({len(self.pending)} task(s) still pending)"


# Task types whose completion the CLI itself resumes the turn for — mirrors
# claude_agent_sdk._internal.query.DEFERRING_TASK_TYPES (not public API, so
# duplicated here rather than imported).
# https://github.com/anthropics/claude-agent-sdk-python/blob/bc0c9af676d9a63ac20a98cf1b7ba4794382c3cc/src/claude_agent_sdk/_internal/query.py#L38-L52
_DEFERRING_TASK_TYPES = frozenset({"local_agent", "local_workflow"})


def _truncate(s: str, n: int = 500) -> str:
    return s if len(s) <= n else s[:n] + f"... [{len(s) - n} more chars]"


class Reporter:
    """Routes streamed claude-agent-sdk messages to stdout and/or a log file."""

    def __init__(self, verbose: bool, log_path: Path | None):
        self.verbose = verbose
        self._log = log_path.open("w", encoding="utf-8") if log_path else None
        self._turn = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._log:
            self._log.close()

    def header(self, title: str) -> None:
        """Emit a section header (e.g. ``"bug 12345"``) and reset the turn count."""
        self._turn = 0
        banner = f"\n{'#' * 60}\n# {title}\n{'#' * 60}"
        self._emit(banner, always=True)

    def _emit(self, line: str, *, always: bool = False, full: str | None = None):
        if self._log:
            self._log.write((full if full is not None else line) + "\n")
            self._log.flush()
        if always or self.verbose:
            print(line)

    def message(self, msg) -> None:
        if isinstance(msg, AssistantMessage):
            is_main = msg.parent_tool_use_id is None
            label = "agent" if is_main else "subagent"
            if is_main:
                self._turn += 1
                self._emit(f"\n--- turn {self._turn} ---")
            for block in msg.content:
                if isinstance(block, TextBlock):
                    self._emit(f"\n[{label}] {block.text}", always=is_main)
                elif isinstance(block, ThinkingBlock):
                    thinking = block.thinking.strip()
                    snippet = thinking.split("\n", 1)[0]
                    self._emit(
                        f"[{label}:thinking] {_truncate(snippet, 120)}",
                        full=f"[{label}:thinking]\n{thinking}",
                    )
                elif isinstance(block, ToolUseBlock):
                    inp = json.dumps(block.input, default=str)
                    inp_full = json.dumps(block.input, indent=2, default=str)
                    self._emit(
                        f"[{label}→tool] {block.name}({_truncate(inp, 300)})",
                        full=f"[{label}→tool] {block.name}\n{inp_full}",
                    )

        elif isinstance(msg, UserMessage):
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        marker = "ERROR" if block.is_error else "ok"
                        if isinstance(block.content, str):
                            text = block.content
                        elif isinstance(block.content, list):
                            parts = [
                                c.get("text", "")
                                for c in block.content
                                if isinstance(c, dict) and c.get("type") == "text"
                            ]
                            text = "\n".join(parts)
                        else:
                            text = str(block.content)
                        self._emit(
                            f"  [tool←{marker}] {_truncate(text, 400)}",
                            full=f"  [tool←{marker}]\n{text}",
                        )

        elif isinstance(msg, SystemMessage):
            if msg.subtype == "init":
                model = msg.data.get("model", "?")
                self._emit(f"[system] session started (model={model})")
            else:
                data = json.dumps(msg.data, default=str)
                self._emit(
                    f"[system:{msg.subtype}] {_truncate(data, 200)}",
                    full=f"[system:{msg.subtype}] {data}",
                )

        elif isinstance(msg, ResultMessage):
            self._emit(f"\n{'=' * 60}", always=True)
            if msg.total_cost_usd:
                line = f"[done] turns={msg.num_turns} cost=${msg.total_cost_usd:.4f}"
            else:
                line = f"[done] turns={msg.num_turns}"
            self._emit(line, always=True)
            if msg.is_error:
                self._emit(f"[done] ERROR: {msg.result}", always=True)


async def receive_settled_response(
    client: ClaudeSDKClient,
    on_message: Callable[["Message"], None] | None = None,
    *,
    timeout_s: float = 3600,
) -> ResultMessage:
    """Drive ``client`` to a *settled* :class:`ResultMessage`.

    ``client.receive_response()`` stops at the first ``ResultMessage``, but
    the CLI can emit one while a task the agent backgrounded is still
    running, reporting the turn "done" prematurely (see
    anthropics/claude-agent-sdk-python#1138). This drains
    ``client.receive_messages()`` instead (it doesn't stop at a
    ``ResultMessage``) and only returns once one arrives with no *deferring*
    task (``local_agent``/``local_workflow``, see ``_DEFERRING_TASK_TYPES``)
    still open — backgrounded shells and Monitor watches run forever by
    design and are never waited on. A task's terminal state can arrive as
    either a ``TaskNotificationMessage`` or a ``TaskUpdatedMessage``, so both
    clear it.

    Args:
        client: A connected client with a query already sent.
        on_message: Called with each message as it streams in, before this
            function's own bookkeeping. Optional.
        timeout_s: Bounds the wait so a task that never settles raises
            ``UnsettledResponseError`` instead of hanging. Defaults to an
            hour (a full Firefox build); pass a larger value for
            longer-running work.

    Raises:
        UnsettledResponseError: timed out, or the connection ended before
            any ``ResultMessage`` arrived.
    """
    pending: dict[str, str] = {}
    result_msg: ResultMessage | None = None

    try:
        async with asyncio.timeout(timeout_s):
            async for msg in client.receive_messages():
                if on_message is not None:
                    on_message(msg)

                if isinstance(msg, TaskStartedMessage):
                    if msg.task_type in _DEFERRING_TASK_TYPES:
                        pending[msg.task_id] = msg.description
                elif isinstance(msg, (TaskNotificationMessage, TaskUpdatedMessage)):
                    if msg.status in TERMINAL_TASK_STATUSES:
                        pending.pop(msg.task_id, None)
                elif isinstance(msg, ResultMessage):
                    result_msg = msg
                    if not pending:
                        return result_msg
    except TimeoutError as exc:
        raise UnsettledResponseError(
            f"timed out after {timeout_s:.0f}s waiting for the response to settle",
            pending,
        ) from exc

    raise UnsettledResponseError(
        "connection ended before a settled ResultMessage arrived", pending
    )
