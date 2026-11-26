"""Weave tracing for Codex SDK agents.

Records turns, model requests, token usage, and tool calls in Weave.
Uses the agent name and run ID from ``trace_agent``.

Subagent activity isn't captured beyond the tool call that starts it.

Requires hackbot-runtime's ``codex-sdk`` optional extra.
"""

import contextlib
import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone

from openai_codex import AsyncTurnHandle, TurnHandle
from openai_codex.generated.v2_all import (
    AgentMessageThreadItem,
    CollabAgentToolCallThreadItem,
    CommandExecutionThreadItem,
    DynamicToolCallThreadItem,
    FileChangeThreadItem,
    InputTextDynamicToolCallOutputContentItem,
    ItemCompletedNotification,
    ItemStartedNotification,
    McpToolCallThreadItem,
    MessagePhase,
    ModelReroutedNotification,
    ReasoningThreadItem,
    TextUserInput,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
    TurnStatus,
    UserMessageThreadItem,
    WebSearchThreadItem,
)
from weave.conversation import Conversation, Message, Reasoning, Usage
from weave.conversation.agent_context import resolve_agent_name
from weave.conversation.types import ToolCallPart

log = logging.getLogger("hackbot_runtime")

# Turn spans are named this unless agent_name_override() gives the agent's name.
DEFAULT_AGENT_NAME = "codex"

TOOL_ITEMS = (
    CollabAgentToolCallThreadItem,
    CommandExecutionThreadItem,
    DynamicToolCallThreadItem,
    FileChangeThreadItem,
    McpToolCallThreadItem,
    WebSearchThreadItem,
)


def _tool_call(item) -> tuple[str, object]:
    """The tool name and arguments to show for a tool item."""
    if isinstance(item, CommandExecutionThreadItem):
        return "shell", {"command": item.command, "cwd": item.cwd.root}
    if isinstance(item, McpToolCallThreadItem):
        return f"{item.server}.{item.tool}", item.arguments
    if isinstance(item, DynamicToolCallThreadItem):
        name = f"{item.namespace}.{item.tool}" if item.namespace else item.tool
        return name, item.arguments
    if isinstance(item, FileChangeThreadItem):
        return "apply_patch", {"paths": [change.path for change in item.changes]}
    if isinstance(item, WebSearchThreadItem):
        return "web_search", {"query": item.query}
    if isinstance(item, CollabAgentToolCallThreadItem):
        return f"collab.{item.tool.value}", {"prompt": item.prompt, "model": item.model}


def _tool_result(item) -> tuple[str, bool]:
    """A completed tool item's result text, and whether the call failed."""
    # web search items have no status
    status = getattr(item, "status", None)

    # Unsuccessful status values across Codex tool types.
    failed = status is not None and status.value in (
        "failed",
        "declined",
        "interrupted",
    )
    # Return the output and whether the tool failed.
    if isinstance(item, CommandExecutionThreadItem):
        failed = failed or item.exit_code not in (None, 0)
        return item.aggregated_output or "", failed
    if isinstance(item, McpToolCallThreadItem):
        if item.error is not None:
            return item.error.message, True
        parts = []
        # MCP results can include non-text content, extract the text.
        if item.result is not None:
            for c in item.result.content:
                if isinstance(c, dict) and c.get("type") == "text":
                    parts.append(c.get("text", ""))
        return "\n".join(parts), failed
    if isinstance(item, DynamicToolCallThreadItem):
        parts = []
        # Dynamic tool output can also include non-text content
        for content in item.content_items or []:
            if isinstance(content.root, InputTextDynamicToolCallOutputContentItem):
                parts.append(content.root.text)
        return "\n".join(parts), failed or item.success is False
    if isinstance(item, FileChangeThreadItem):
        # Combine the diffs from all changed files.
        return "\n".join(change.diff for change in item.changes), failed
    # Web search and collab calls have no output to show.
    return "", failed


class _TurnTrace:
    """Records one turn's notifications as Weave spans."""

    def __init__(self, conversation: Conversation, model: str, totals: dict):
        self._conversation = conversation
        self._model = model
        # The shared dictionary of previous token totals
        # used to avoid counting repeated updates (e.g. resent in rate limit update)
        self._totals = totals
        self._turn = conversation.start_turn(
            agent_name=resolve_agent_name(DEFAULT_AGENT_NAME), model=model
        )
        # No model-request span is open yet.
        self._chat = None
        self._outputs: list[Message] = []
        self._reasoning: list[str] = []
        # Tool spans still waiting for completion, stored by tool-call ID
        # since a tool's start and completion comes separately and we have
        # to attach the result to the correct tool call
        self._tools = {}
        # The explicitly marked final answer
        self._answer = None
        # The latest assistant message without a phase label
        # (to avoid explicitly labelled commentary messages), fallback answer
        self._last_message = None
        self._error = None

    def __enter__(self):
        # Enter Weave turn context so it stays active during the block.
        self._turn.__enter__()
        return self

    def __exit__(self, *exc):
        try:
            self._finish()
        except Exception:
            log.exception("Codex trace failed to finish the turn")
        return self._turn.__exit__(*exc)

    def record(self, event) -> None:
        """Record one notification; never lets a tracing bug break the stream."""
        try:
            self._record(event.payload)
        except Exception:
            log.exception("Codex trace failed to record %s", event.method)

    # Ensure there is a model-request span for the activity being collected.
    def _start_chat(self) -> None:
        """Open the chat span for the model request now in progress."""
        # multiple notifications could belong to one request, so do not open a span twice.
        if self._chat is None:
            self._chat = self._turn.start_llm(model=self._model, provider_name="openai")
            self._chat.started_at = datetime.now(timezone.utc)

    def _end_chat(self, usage: Usage | None = None) -> None:
        chat = self._chat
        self._chat = None
        if chat is None:
            return
        reasoning_text = "\n".join(self._reasoning)
        reasoning = None

        if reasoning_text:
            reasoning = Reasoning(content=reasoning_text)

        with chat:
            chat.record(
                output_messages=self._outputs,
                reasoning=reasoning,
                usage=usage,
            )
        self._outputs = []
        self._reasoning = []

    def _record(self, payload) -> None:
        if isinstance(payload, ItemStartedNotification):
            item = payload.item.root
            if isinstance(item, UserMessageThreadItem):
                texts = []
                for part in item.content:
                    if isinstance(part.root, TextUserInput):
                        texts.append(part.root.text)
                self._turn.record(messages=[Message.user("\n".join(texts))])
                return
            self._start_chat()
            if isinstance(item, TOOL_ITEMS):
                name, arguments = _tool_call(item)
                tool = self._turn.start_tool(
                    name=name,
                    arguments=json.dumps(arguments, default=str),
                    tool_call_id=item.id,
                )
                tool.started_at = datetime.now(timezone.utc)
                self._tools[item.id] = tool

        elif isinstance(payload, ItemCompletedNotification):
            item = payload.item.root
            if isinstance(item, AgentMessageThreadItem):
                self._start_chat()
                self._outputs.append(Message.assistant(item.text))
                # same rule as the SDK's TurnResult.final_response
                if item.phase == MessagePhase.final_answer:
                    self._answer = item.text
                elif item.phase is None:
                    self._last_message = item.text
            elif isinstance(item, ReasoningThreadItem):
                self._start_chat()
                summary = item.summary or []
                content = item.content or []

                text = "\n".join(summary + content)
                text = text.strip()

                if text:
                    self._reasoning.append(text)
            elif isinstance(item, TOOL_ITEMS):
                self._start_chat()
                name, arguments = _tool_call(item)
                arguments = json.dumps(arguments, default=str)
                tool_call = ToolCallPart(
                    id=item.id,
                    name=name,
                    arguments=arguments,
                )
                message = Message.assistant(tool_calls=[tool_call])
                self._outputs.append(message)

                tool = self._tools.pop(item.id, None)
                if tool is not None:
                    result, failed = _tool_result(item)
                    with tool:
                        # some calls only know their arguments once completed (e.g. web search)
                        tool.arguments = arguments
                        tool.result = result
                        if failed:
                            tool.record_error(RuntimeError("tool reported an error"))

        elif isinstance(payload, ThreadTokenUsageUpdatedNotification):
            thread = payload.thread_id
            total = payload.token_usage.total.total_tokens
            last = payload.token_usage.last
            previous = self._totals.get(thread)
            self._totals[thread] = total
            # Skip context-size estimates, which have no input tokens.
            if (previous is None or total > previous) and last.input_tokens > 0:
                self._start_chat()
                self._end_chat(
                    Usage(
                        input_tokens=last.input_tokens,
                        cache_read_input_tokens=last.cached_input_tokens,
                        output_tokens=last.output_tokens,
                        reasoning_tokens=last.reasoning_output_tokens,
                        total_tokens=last.total_tokens,
                    )
                )

        elif isinstance(payload, ModelReroutedNotification):
            self._model = payload.to_model
            self._turn.record(model=payload.to_model)

        elif isinstance(payload, TurnCompletedNotification):
            turn = payload.turn
            if turn.status != TurnStatus.completed:
                self._error = (
                    turn.error.message
                    if turn.error is not None
                    else f"turn {turn.status.value}"
                )

    def _finish(self) -> None:
        """End the open chat and tool spans, and record the turn's outcome."""
        self._end_chat()
        for tool in self._tools.values():
            with tool:
                pass
        self._tools.clear()
        answer = self._answer if self._answer is not None else self._last_message
        if answer:
            self._turn.record(output_messages=[Message.assistant(answer)])
        if self._error is not None:
            self._turn.record_error(RuntimeError(self._error))


def _conversation(thread_id: str, model: str) -> Conversation:
    return Conversation(
        conversation_id=thread_id,
        agent_name=DEFAULT_AGENT_NAME,
        model=model,
        continue_parent_trace=True,
    )


@contextlib.contextmanager
def trace_codex_turns() -> Iterator[None]:
    """Trace every Codex turn streamed while active, sync or async.

    Patches ``stream()`` on both turn handle types (``run()`` streams through
    it), and restores them on exit.
    """
    # Per-thread token totals, shared by the thread's turns.
    totals: dict[str, int] = {}
    async_stream = AsyncTurnHandle.stream
    sync_stream = TurnHandle.stream

    async def traced_async_stream(self):
        model = ""
        try:
            # The thread's model, for Weave to price the chat spans by.
            model = (await self._codex._client.thread_read(self.thread_id)).thread.model
        except Exception:
            log.exception("Codex trace could not read the thread's model")
        with (
            _conversation(self.thread_id, model or "") as conversation,
            _TurnTrace(conversation, model or "", totals) as trace,
        ):
            async for event in async_stream(self):
                trace.record(event)
                yield event

    def traced_sync_stream(self):
        model = ""
        try:
            model = self._client.thread_read(self.thread_id).thread.model
        except Exception:
            log.exception("Codex trace could not read the thread's model")
        with (
            _conversation(self.thread_id, model or "") as conversation,
            _TurnTrace(conversation, model or "", totals) as trace,
        ):
            for event in sync_stream(self):
                trace.record(event)
                yield event

    AsyncTurnHandle.stream = traced_async_stream
    TurnHandle.stream = traced_sync_stream
    try:
        yield
    finally:
        AsyncTurnHandle.stream = async_stream
        TurnHandle.stream = sync_stream
