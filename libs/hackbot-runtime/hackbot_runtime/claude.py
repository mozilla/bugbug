"""Shared claude-agent-sdk helpers for hackbot agents.

Generic, agent-neutral building blocks that every claude-agent-sdk agent would
otherwise copy verbatim. Agents still assemble their own ``ClaudeAgentOptions``
and drive the ``ClaudeSDKClient`` loop — these just remove the boilerplate of
rendering the streamed messages.

Requires the ``claude-sdk`` optional extra of hackbot-runtime.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


def _truncate(s: str, n: int = 500) -> str:
    return s if len(s) <= n else s[:n] + f"... [{len(s) - n} more chars]"


def _stamp() -> str:
    """UTC wall clock, HH:MM:SS.mmm, prefixed to every log-file record."""
    now = time.time()
    clock = time.strftime("%H:%M:%S", time.gmtime(now))
    millis = int(now * 1000) % 1000
    return f"{clock}.{millis:03d}"


class Reporter:
    """Routes streamed claude-agent-sdk messages to stdout and/or a log file."""

    def __init__(self, verbose: bool, log_path: Path | None):
        self.verbose = verbose
        self._log = log_path.open("w", encoding="utf-8") if log_path else None
        self._turn = 0
        self._started = time.monotonic()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._log:
            self._log.close()

    def header(self, title: str) -> None:
        """Emit a section header (e.g. ``"bug 12345"``) and reset the turn count."""
        self._turn = 0
        self._started = time.monotonic()
        banner = f"\n{'#' * 60}\n# {title}\n# started {_stamp()} UTC\n{'#' * 60}"
        self._emit(banner, always=True, stamp=False)

    def _emit(
        self,
        line: str,
        *,
        always: bool = False,
        full: str | None = None,
        stamp: bool = True,
    ):
        if self._log:
            text = full if full is not None else line
            lead, text = ("\n", text[1:]) if text.startswith("\n") else ("", text)
            prefix = f"{_stamp()} " if stamp else ""
            self._log.write(f"{lead}{prefix}{text}\n")
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
                    self._emit(f"[{label}] {block.text}", always=is_main)
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
                            f"[tool←{marker}] {_truncate(text, 400)}",
                            full=f"[tool←{marker}]\n{text}",
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
            self._emit(f"\n{'=' * 60}", always=True, stamp=False)
            line = f"[done] turns={msg.num_turns}"
            if msg.total_cost_usd:
                line += f" cost=${msg.total_cost_usd:.4f}"
            line += f" elapsed={time.monotonic() - self._started:.0f}s"
            self._emit(line, always=True)
            if msg.is_error:
                self._emit(f"[done] ERROR: {msg.result}", always=True)
