"""Frontend triage tool -- a read-only Bugzilla triage + fix-planning agent.

Orchestrates a Claude agent that triages Firefox desktop *frontend* bugs
according to rulesets in the rules/ directory. The agent investigates the
source repository READ-ONLY (no build, no source edits, no reproduction) and
produces a root-cause analysis plus a proposed fix plan, which it records as a
Bugzilla comment for a human (or a downstream execution agent) to act on.

It reaches Bugzilla via an out-of-process MCP broker (HTTP transport) that holds
the Bugzilla token -- the agent process itself never sees it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from agent_tools import mozilla_vcs, searchfox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.mozilla_vcs import MozillaVcsContext
from agent_tools.searchfox import SearchfoxContext
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
from searchfox import AsyncSearchfoxClient

from .config import (
    BUGZILLA_READ_TOOLS,
    ENABLED_ACTION_TYPES,
    MOZILLA_VCS_TOOLS,
    SEARCHFOX_TOOLS,
)

HERE = Path(__file__).resolve().parent

# The agent is asked to end its final message with a fenced ```json block
# carrying the structured plan. We parse the last such block so the result is
# machine-consumable for downstream handoff (summary.json -> execution agent).
_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


class FrontendTriageResult(HackbotAgentResult):
    bug_id: int
    # Structured plan (best-effort, parsed from the agent's final message).
    summary: str | None = None
    root_cause: str | None = None
    proposed_fix: str | None = None
    target_files: list[str] | None = None
    confidence: str | None = None
    # Handoff fields for a downstream executor agent.
    actionable: bool | None = None  # false => out of scope / nothing to fix-plan
    regressor_node: str | None = None  # hg node of the introducing changeset, if found
    relevant_tests: list[str] | None = (
        None  # existing tests covering the area (verify anchor)
    )
    # The agent's full final message, always present as a fallback.
    result: str | None = None


def load_system_prompt(rules_dir: Path, extra: str) -> str:
    tmpl = (HERE / "prompts" / "system.md").read_text()

    return tmpl.format(
        rules_dir=str(rules_dir.resolve()),
        extra_instructions=extra or "(none)",
    )


def make_investigator() -> AgentDefinition:
    """Create a single generic, read-only investigator subagent definition."""
    return AgentDefinition(
        description=(
            "Focused investigator for answering a specific question about "
            "a bug or the source tree. The main agent writes your complete "
            "instructions at spawn time -- follow them precisely and return "
            "only what was asked for."
        ),
        prompt=(
            "You are a focused investigator subagent. You will be given a "
            "self-contained task by the triage agent. Complete it and return "
            "a concise answer. You have read-only access only: do not modify "
            "the source tree or Bugzilla. Do not speculate beyond what you can "
            "verify."
        ),
        tools=[
            "Read",
            "Grep",
            "Glob",
            "Bash",
            *BUGZILLA_READ_TOOLS,
            *SEARCHFOX_TOOLS,
            *MOZILLA_VCS_TOOLS,
        ],
        model="inherit",
    )


def parse_plan(text: str | None) -> dict:
    """Extract the structured plan from the agent's final message, if present.

    Returns an empty dict when no parseable ```json block is found -- the raw
    text is still preserved in ``FrontendTriageResult.result``.
    """
    if not text:
        return {}
    matches = _JSON_BLOCK.findall(text)
    if not matches:
        return {}
    try:
        data = json.loads(matches[-1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}

    def _as_list(value):
        if isinstance(value, str):
            return [value]
        return value if isinstance(value, list) else None

    actionable = data.get("actionable")
    if not isinstance(actionable, bool):
        actionable = None
    return {
        "summary": data.get("summary"),
        "root_cause": data.get("root_cause"),
        "proposed_fix": data.get("proposed_fix"),
        "target_files": _as_list(data.get("target_files")),
        "confidence": data.get("confidence"),
        "actionable": actionable,
        "regressor_node": data.get("regressor_node"),
        "relevant_tests": _as_list(data.get("relevant_tests")),
    }


async def run_frontend_triage(
    *,
    bugzilla_mcp_server: McpServerConfig,
    source_repo: Path,
    bug: int,
    instructions: str = "",
    task: str | None = None,
    rules_dir: Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    effort: str | None = None,
    verbose: bool = False,
    log: Path | None = None,
    actions_recorder: ActionsRecorder | None = None,
) -> FrontendTriageResult:
    """Triage and plan a fix for a single Bugzilla frontend bug (read-only).

    Returns a :class:`FrontendTriageResult` on success; raises
    :class:`AgentError` if the agent ends in an error.
    """
    if rules_dir is None:
        rules_dir = HERE / "rules"

    print(f"[frontend_triage] triaging bug {bug}", file=sys.stderr)

    # Action-recording MCP server (in-process). Standalone/script runs pass
    # actions_recorder=None and get a local recorder (no uploader).
    actions_recorder, actions_server = actions_server_for(
        actions_recorder, types=ENABLED_ACTION_TYPES
    )
    enabled_action_tools = actions_to_tool_names(ENABLED_ACTION_TYPES)

    # In-process MCP servers for read-only code investigation. Searchfox and HGMO
    # are public (no credentials), so they run in-process rather than via a
    # brokered sidecar.
    searchfox_server = build_sdk_server(
        "searchfox", SearchfoxContext(client=AsyncSearchfoxClient()), searchfox.TOOLS
    )
    vcs_server = build_sdk_server("mozilla_vcs", MozillaVcsContext(), mozilla_vcs.TOOLS)

    system_prompt = load_system_prompt(rules_dir, instructions)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={
            "bugzilla": bugzilla_mcp_server,
            "searchfox": searchfox_server,
            "mozilla_vcs": vcs_server,
            ACTIONS_SERVER_NAME: actions_server,
        },
        agents={"investigator": make_investigator()},
        cwd=str(source_repo.resolve()),
        add_dirs=[str(rules_dir.resolve())],
        permission_mode="bypassPermissions",
        # Read-only investigation tools only: no Write/Edit (source is never
        # modified) and no firefox build/eval tools.
        allowed_tools=[
            "Read",
            "Grep",
            "Glob",
            "Bash",
            "Task",
            *BUGZILLA_READ_TOOLS,
            *SEARCHFOX_TOOLS,
            *MOZILLA_VCS_TOOLS,
            *enabled_action_tools,
        ],
        model=model,
        max_turns=max_turns,
        **({"effort": effort} if effort else {}),
        setting_sources=[],
    )

    rules_path = rules_dir.resolve()
    if task:
        user_prompt = (
            f"Bug to work on: {bug}\n\n"
            f"Task: {task}\n\n"
            f"The rules in {rules_path} are available if the task "
            f"calls for them, but the task above is your primary "
            f"directive -- it overrides the default triage workflow."
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

    plan = parse_plan(result_msg.result)

    return FrontendTriageResult(
        bug_id=bug,
        result=result_msg.result,
        num_turns=result_msg.num_turns,
        total_cost_usd=result_msg.total_cost_usd,
        **plan,
    )
