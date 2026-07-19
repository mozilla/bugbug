"""Bug fix triage tool -- a Bugzilla triage agent.

Orchestrates a Claude agent that triages bugs according to rulesets
in the rules/ directory. The agent has access to a source repository
and reaches Bugzilla via an out-of-process MCP broker (HTTP transport)
that holds the Bugzilla token — the agent process itself never sees it.
"""

from __future__ import annotations

import sys
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
from hackbot_runtime import ActionsRecorder, AgentError, HackbotAgentResult
from hackbot_runtime.actions import ACTIONS_SERVER_NAME
from hackbot_runtime.actions.claude_sdk import actions_server_for, actions_to_tool_names
from hackbot_runtime.claude import Reporter

from .config import (
    BUGZILLA_READ_TOOLS,
    ENABLED_ACTION_TYPES,
    FIREFOX_TOOLS,
    SOURCE_WRITE_TOOLS,
)

HERE = Path(__file__).resolve().parent


class BugFixResult(HackbotAgentResult):
    bug_id: int
    result: str | None = None


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
    bug: int,
    instructions: str = "",
    task: str | None = None,
    revision_id: int | None = None,
    rules_dir: Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    effort: str | None = None,
    verbose: bool = False,
    log: Path | None = None,
    actions_recorder: ActionsRecorder | None = None,
) -> BugFixResult:
    """Triage and fix a single Bugzilla bug with a claude-agent-sdk agent.

    Returns a :class:`BugFixResult` on success; raises :class:`AgentError` if the
    agent ends in an error.
    """
    if rules_dir is None:
        rules_dir = HERE / "rules"

    print(f"[bug_fix] triaging bug {bug}", file=sys.stderr)

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
    enabled_action_tools = actions_to_tool_names(ENABLED_ACTION_TYPES)

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
            *enabled_action_tools,
            *FIREFOX_TOOLS,
        ],
        model=model,
        max_turns=max_turns,
        **({"effort": effort} if effort else {}),
        setting_sources=[],
    )

    rules_path = rules_dir.resolve()
    if revision_id:
        # Follow-up mode: a reviewer commented on an existing revision (the
        # comment is in the system prompt's extra instructions). Address it and
        # update that same revision rather than opening a new one.
        user_prompt = (
            f"Follow up on Phabricator revision D{revision_id} for bug {bug}.\n\n"
            f"A reviewer left a comment (see the instructions in your system "
            f"prompt). Address it: investigate, make the necessary source "
            f"changes, and verify the fix. When you are done, submit your "
            f"changes by calling submit_patch with revision_id={revision_id} so "
            f"the existing revision D{revision_id} is updated — do not create a "
            f"new revision.\n\n"
            f"Consult the relevant rules in {rules_path} if they apply."
        )
    elif task:
        user_prompt = (
            f"Bug to work on: {bug}\n\n"
            f"Task: {task}\n\n"
            f"The rules in {rules_path} are available if the task "
            f"calls for them, but the task above is your primary "
            f"directive — it overrides the default triage workflow."
        )
    else:
        user_prompt = (
            f"Triage bug {bug}.\n\nConsult the relevant rules in {rules_path}."
        )

    result_msg: ResultMessage | None = None
    with Reporter(verbose=verbose, log_path=log) as reporter:
        reporter.header(f"bug {bug}")
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_prompt)
            async for msg in client.receive_response():
                reporter.message(msg)
                if isinstance(msg, ResultMessage):
                    result_msg = msg

    if result_msg is None:
        raise AgentError(f"bug {bug}: agent produced no result message")
    if result_msg.is_error:
        raise AgentError(
            f"bug {bug} triage failed: {result_msg.result or result_msg.subtype}"
        )

    return BugFixResult(
        bug_id=bug,
        result=result_msg.result,
        num_turns=result_msg.num_turns,
        total_cost_usd=result_msg.total_cost_usd,
    )
