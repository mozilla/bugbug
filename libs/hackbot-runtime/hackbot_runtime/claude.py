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
    """Used for when ``receive_settled_response`` gave up before the agent's turn settled."""


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

    ``client.receive_response()`` stops at the first ``ResultMessage`` it
    sees, but the CLI can emit one while a task the agent started with
    ``run_in_background`` (a background ``Bash`` command or subagent) is
    still running, reporting the turn "done" even though the agent meant to
    act on that task's result before finishing.
    See https://github.com/anthropics/claude-agent-sdk-python/issues/1138

    Per the SDK's own task-lifecycle contract, a still-running task's
    completion resumes the conversation with a further turn on the same
    connection — the fix is to keep listening, not to intervene. This drains
    ``client.receive_messages()`` (which, unlike ``receive_response()``, does
    not stop at a ``ResultMessage``) and only returns once a ``ResultMessage``
    arrives with no *deferring* task started during this call still
    unresolved — only ``local_agent``/``local_workflow`` tasks count (see
    ``_DEFERRING_TASK_TYPES``); backgrounded shells and Monitor watches can
    run forever by design and are not waited on. A task's terminal state can
    arrive as either a ``TaskNotificationMessage`` or a ``TaskUpdatedMessage``
    (never both, for some task types), so both clear it from the pending set.

    Args:
        client: A connected client with a query already sent.
        on_message: Called with every message as it streams in (e.g. to log
            it), before this function's own bookkeeping. Optional.
        timeout_s: Bounds the whole wait, so a task that never reports
            completion surfaces as an ``UnsettledResponseError`` instead of
            hanging the run indefinitely. Defaults to an hour to comfortably
            cover a full Firefox build; pass a larger value for agents that
            background longer-running work.

    Raises:
        UnsettledResponseError: ``timeout_s`` elapsed before the response
            settled, or the connection ended before any ``ResultMessage`` was
            seen at all.
    """
    pending: dict[str, str] = {}
    result_msg: ResultMessage | None = None

    def _pending_suffix() -> str:
        return f" ({len(pending)} task(s) still pending)" if pending else ""

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
            f"timed out after {timeout_s:.0f}s waiting for the response to "
            f"settle{_pending_suffix()}"
        ) from exc

    raise UnsettledResponseError(
        f"connection ended before a settled ResultMessage arrived{_pending_suffix()}"
    )
