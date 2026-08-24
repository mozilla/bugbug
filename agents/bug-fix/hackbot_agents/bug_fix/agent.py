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
    BUGZILLA_NEEDINFO_ACTIONS,
    BUGZILLA_READ_TOOLS,
    FIREFOX_TOOLS,
    PHABRICATOR_FOLLOW_UP_ACTIONS,
    PHABRICATOR_READ_TOOLS,
    SOURCE_WRITE_TOOLS,
    TRIAGE_AND_FIX_ACTIONS,
)

HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "prompts"


class BugFixResult(HackbotAgentResult):
    bug_id: int
    result: str | None = None


def render_prompt(name: str, **fields: object) -> str:
    """Render a prompt template from ``prompts/`` via ``str.format``.

    Prompt text lives in ``prompts/*.md`` rather than inline in Python, so it
    stays readable and editable. Substituted values are inserted verbatim
    (``str.format`` does not re-scan them), so an untrusted ``comment`` cannot
    break out of its ``{comment}`` placeholder.
    """
    return (PROMPTS / name).read_text().format(**fields)


def select_workflow(
    *,
    bug: int,
    revision_id: int | None,
    comment: str | None,
    bugzilla_needinfo_flag_id: int | None,
    rules_dir: Path,
) -> tuple[list[str], str]:
    """Select actions and prompt for exactly one of the three bug-fix modes."""
    if bugzilla_needinfo_flag_id is not None:
        return BUGZILLA_NEEDINFO_ACTIONS, render_prompt(
            "bugzilla-needinfo.md", bug_id=bug, comment=comment
        )
    if revision_id:
        return PHABRICATOR_FOLLOW_UP_ACTIONS, render_prompt(
            "follow-up.md", revision_id=revision_id, bug_id=bug, comment=comment
        )
    return TRIAGE_AND_FIX_ACTIONS, render_prompt(
        "triage-and-fix.md", bug_id=bug, rules_path=str(rules_dir.resolve())
    )


def _record_needinfo_clear(
    recorder: ActionsRecorder, *, bug_id: int, flag_id: int | None
) -> None:
    """Record the clear after responding to a Bugzilla needinfo webhook."""
    if flag_id is None or not recorder.actions:
        return

    recorder.record(
        "bugzilla.update_bug",
        {
            "bug_id": bug_id,
            "changes": {"flags": [{"id": flag_id, "status": "X"}]},
        },
        reasoning="Clear the needinfo flag that triggered this response.",
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
    phabricator_mcp_server: McpServerConfig,
    source_repo: Path,
    fx_ctx: FirefoxContext,
    bug: int,
    comment: str | None = None,
    revision_id: int | None = None,
    bugzilla_needinfo_flag_id: int | None = None,
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

    action_types, user_prompt = select_workflow(
        bug=bug,
        revision_id=revision_id,
        comment=comment,
        bugzilla_needinfo_flag_id=bugzilla_needinfo_flag_id,
        rules_dir=rules_dir,
    )

    # Action-recording MCP server (in-process). Standalone/script runs pass
    # actions_recorder=None and get a local recorder that copies attachments
    # under ./artifacts (no uploader).
    actions_recorder, actions_server = actions_server_for(
        actions_recorder, types=action_types
    )
    enabled_action_tools = actions_to_tool_names(action_types)

    system_prompt = render_prompt("system.md", rules_dir=str(rules_dir.resolve()))

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={
            "bugzilla": bugzilla_mcp_server,
            "phabricator": phabricator_mcp_server,
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
            *PHABRICATOR_READ_TOOLS,
            *enabled_action_tools,
            *FIREFOX_TOOLS,
        ],
        model=model,
        max_turns=max_turns,
        **({"effort": effort} if effort else {}),
        setting_sources=[],
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

    _record_needinfo_clear(
        actions_recorder,
        bug_id=bug,
        flag_id=bugzilla_needinfo_flag_id,
    )

    return BugFixResult(
        bug_id=bug,
        result=result_msg.result,
        num_turns=result_msg.num_turns,
        total_cost_usd=result_msg.total_cost_usd,
    )
