"""Bug fix triage tool -- a Bugzilla triage agent.

Orchestrates a Claude agent that triages bugs according to rulesets
in the rules/ directory. The agent has access to a source repository
and reaches Bugzilla via an out-of-process MCP broker (HTTP transport)
that holds the Bugzilla token — the agent process itself never sees it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from agent_tools import firefox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.firefox import FirefoxContext
from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpServerConfig,
    ResultMessage,
)
from hackbot_runtime import ActionsRecorder
from hackbot_runtime.actions import ACTIONS_SERVER_NAME
from hackbot_runtime.actions.claude_sdk import actions_server_for
from hackbot_runtime.claude import Reporter

from .config import (
    BUGZILLA_READ_TOOLS,
    ENABLED_ACTION_TOOLS,
    ENABLED_ACTION_TYPES,
    FIREFOX_TOOLS,
    SOURCE_WRITE_TOOLS,
)

HERE = Path(__file__).resolve().parent


@dataclass
class BugFixResult:
    exit_code: int = 0
    bugs_processed: int = 0


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


async def run_bug_fix(
    *,
    bugzilla_mcp_server: McpServerConfig,
    source_repo: Path,
    fx_ctx: FirefoxContext,
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
    """Triage and fix the given Bugzilla bug(s) with a claude-agent-sdk agent."""
    if rules_dir is None:
        rules_dir = HERE / "rules"

    if not bugs:
        print("[bug_fix] no bug ids supplied — nothing to do", file=sys.stderr)
        return BugFixResult(exit_code=0)

    selected = sorted(bugs, reverse=newest_first)
    print(f"[bug_fix] triaging {len(selected)} bug(s): {selected}", file=sys.stderr)

    # Firefox build/eval MCP server (in-process; no tokens). The runtime
    # derives fx_ctx from the prepared source checkout and the agent's
    # hackbot.toml; here we only wrap its tools as an MCP server.
    firefox_server = build_sdk_server("firefox", fx_ctx, firefox.TOOLS)

    # Action-recording MCP server (in-process). Standalone/script runs pass
    # actions_recorder=None and get a local recorder that copies attachments
    # under ./artifacts (no uploader).
    actions_recorder, actions_server = actions_server_for(
        actions_recorder, types=ENABLED_ACTION_TYPES
    )

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

    # Run one fresh agent context per bug.
    exit_code = 0
    rules_path = rules_dir.resolve()
    with Reporter(verbose=verbose, log_path=log) as reporter:
        for i, bug_id in enumerate(selected, 1):
            print(f"[bug_fix] bug {i}/{len(selected)}: {bug_id}", file=sys.stderr)
            reporter.header(f"bug {bug_id}")

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
