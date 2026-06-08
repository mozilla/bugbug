"""Bug fix triage tool -- a Bugzilla triage agent.

Orchestrates a Claude agent that triages bugs according to rulesets
in the rules/ directory. The agent has access to a source repository
and reaches Bugzilla via an out-of-process MCP broker (HTTP transport)
that holds the Bugzilla token — the agent process itself never sees it.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpServerConfig,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from hackbot_runtime import ActionsRecorder
from hackbot_runtime.actions.claude_sdk import build_actions_sdk_server
from hackbot_runtime.actions.naming import ACTIONS_SERVER_NAME

from bugbug.tools.base import GenerativeModelTool
from bugbug.tools.bug_fix.config import (
    BUGZILLA_READ_TOOLS,
    ENABLED_ACTION_TOOLS,
    ENABLED_ACTION_TYPES,
    FIREFOX_TOOLS,
    SOURCE_WRITE_TOOLS,
)
from bugbug.tools.bug_fix.firefox_mcp import FirefoxContext
from bugbug.tools.bug_fix.firefox_mcp import build_server as build_firefox_server

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #


@dataclass
class BugFixResult:
    exit_code: int = 0
    bugs_processed: int = 0


# --------------------------------------------------------------------------- #
# Prompts & agents
# --------------------------------------------------------------------------- #


def load_system_prompt(rules_dir: Path, extra: str) -> str:
    tmpl = (HERE / "prompts" / "system.md").read_text()

    return tmpl.format(
        rules_dir=str(rules_dir.resolve()),
        extra_instructions=extra or "(none)",
    )


def make_investigator() -> AgentDefinition:
    """Create a single generic investigator subagent definition."""
    return AgentDefinition(
        description=(
            "Focused investigator for answering a specific question about "
            "a bug or the source tree. The main agent writes your complete "
            "instructions at spawn time — follow them precisely and return "
            "only what was asked for."
        ),
        prompt=(
            "You are a focused investigator subagent. You will be given a "
            "self-contained task by the triage agent. Complete it and return "
            "a concise answer. Do not make Bugzilla modifications — you have "
            "read-only access. Do not speculate beyond what you can verify."
        ),
        tools=[
            "Read",
            "Grep",
            "Glob",
            "Bash",
            *BUGZILLA_READ_TOOLS,
            *FIREFOX_TOOLS,
        ],
        model="inherit",
    )


# --------------------------------------------------------------------------- #
# Output streaming
# --------------------------------------------------------------------------- #


def _truncate(s: str, n: int = 500) -> str:
    return s if len(s) <= n else s[:n] + f"... [{len(s) - n} more chars]"


class Reporter:
    """Routes streamed agent messages to stdout and/or a log file."""

    def __init__(self, verbose: bool, log_path: Path | None):
        self.verbose = verbose
        self._log = log_path.open("w", encoding="utf-8") if log_path else None
        self._turn = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._log:
            self._log.close()

    def start_bug(self, bug_id: int) -> None:
        self._turn = 0
        header = f"\n{'#' * 60}\n# bug {bug_id}\n{'#' * 60}"
        self._emit(header, always=True)

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


# --------------------------------------------------------------------------- #
# Tool class
# --------------------------------------------------------------------------- #


class BugFixTool(GenerativeModelTool):
    """Bugzilla triage agent using claude-agent-sdk."""

    @classmethod
    def create(cls, **kwargs):
        return cls()

    async def run(
        self,
        *,
        bugzilla_mcp_server: McpServerConfig,
        source_repo: Path,
        bugs: list[int],
        instructions: str = "",
        task: str | None = None,
        rules_dir: Path | None = None,
        newest_first: bool = False,
        model: str | None = None,
        max_turns: int | None = None,
        effort: str | None = None,
        verbose: bool = False,
        log: Path | None = None,
        actions_recorder: ActionsRecorder | None = None,
    ) -> BugFixResult:
        if rules_dir is None:
            rules_dir = HERE / "rules"

        if not bugs:
            print("[bug_fix] no bug ids supplied — nothing to do", file=sys.stderr)
            return BugFixResult(exit_code=0)

        selected = sorted(bugs, reverse=newest_first)
        print(f"[bug_fix] triaging {len(selected)} bug(s): {selected}", file=sys.stderr)

        # --- Firefox build/eval MCP server (in-process; no tokens) -------- #
        fx_ctx = FirefoxContext.from_source_repo(source_repo)
        firefox_server = build_firefox_server(fx_ctx)

        # --- Action-recording MCP server (in-process) --------------------- #
        if actions_recorder is None:
            # Standalone/script runs have no uploader; copy attachments locally.
            actions_recorder = ActionsRecorder(artifacts_dir=Path("artifacts"))
        actions_server = build_actions_sdk_server(
            actions_recorder, types=ENABLED_ACTION_TYPES
        )

        # --- Build agent options ------------------------------------------ #
        system_prompt = load_system_prompt(rules_dir, instructions)

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={
                "bugzilla": bugzilla_mcp_server,
                "firefox": firefox_server,
                ACTIONS_SERVER_NAME: actions_server,
            },
            agents={"investigator": make_investigator()},
            cwd=str(source_repo.resolve()),
            add_dirs=[str(rules_dir.resolve())],
            permission_mode="bypassPermissions",
            allowed_tools=[
                "Read",
                "Grep",
                "Glob",
                "Bash",
                "Task",
                *SOURCE_WRITE_TOOLS,
                *BUGZILLA_READ_TOOLS,
                *ENABLED_ACTION_TOOLS,
                *FIREFOX_TOOLS,
            ],
            model=model,
            max_turns=max_turns,
            **({"effort": effort} if effort else {}),
            setting_sources=[],
        )

        # --- Run: one fresh agent context per bug ------------------------- #
        exit_code = 0
        rules_path = rules_dir.resolve()
        with Reporter(verbose=verbose, log_path=log) as reporter:
            for i, bug_id in enumerate(selected, 1):
                print(f"[bug_fix] bug {i}/{len(selected)}: {bug_id}", file=sys.stderr)
                reporter.start_bug(bug_id)

                if task:
                    user_prompt = (
                        f"Bug to work on: {bug_id}\n\n"
                        f"Task: {task}\n\n"
                        f"The rules in {rules_path} are available if the task "
                        f"calls for them, but the task above is your primary "
                        f"directive — it overrides the default triage workflow."
                    )
                else:
                    user_prompt = (
                        f"Triage bug {bug_id}.\n\n"
                        f"Consult the relevant rules in {rules_path}."
                    )

                async with ClaudeSDKClient(options=options) as client:
                    await client.query(user_prompt)
                    async for msg in client.receive_response():
                        reporter.message(msg)
                        if isinstance(msg, ResultMessage) and msg.is_error:
                            exit_code = 1

        return BugFixResult(
            exit_code=exit_code,
            bugs_processed=len(selected),
        )
