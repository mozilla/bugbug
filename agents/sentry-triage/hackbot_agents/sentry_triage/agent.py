"""Sentry triage tool -- a Sentry triage agent.

Orchestrates a Claude agent that triages Sentry alerts according to rulesets
in the rules/ directory. The agent has access to the Mozilla Sentry instance
via an out-of-process MCP broker (HTTP transport) that holds the Sentry
API token — the agent process itself never sees it.
"""

from __future__ import annotations

import sys
from pathlib import Path

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
    SENTRY_READ_TOOLS,
    TRIAGE_ACTIONS,
)

HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "prompts"


class SentryTriageResult(HackbotAgentResult):
    result: str | None = None


def render_prompt(name: str, **fields: object) -> str:
    """Render a prompt template from ``prompts/`` via ``str.format``.

    Prompt text lives in ``prompts/*.md`` rather than inline in Python, so it
    stays readable and editable. Substituted values are inserted verbatim
    (``str.format`` does not re-scan them), so an untrusted ``comment`` cannot
    break out of its ``{comment}`` placeholder.
    """
    return (PROMPTS / name).read_text().format(**fields)


def make_alert_investigator() -> AgentDefinition:
    """Create a single generic investigator subagent definition."""
    return AgentDefinition(
        description=(
            "Focused investigator for triaging what issues resulted in a "
            "Sentry alert being triggered. The main agent writes your "
            "complete instructions at spawn time — follow them precisely and "
            "return only what was asked for."
        ),
        prompt=(
            "You are a focused investigator subagent. You will be given a "
            "self-contained task by the triage agent. Complete it and return "
            "a concise answer. Do not make any modifications — you have "
            "read-only access. Do not speculate beyond what you can verify."
        ),
        tools=[
            *SENTRY_READ_TOOLS,
        ],
        model="inherit",
    )


def parse_sentry_alert_url_for_issue_and_event_ids(sentry_alert_url: str) -> tuple[str, str]:
    """Parses a Sentry alert URL for the issue and event ids."""
    # TODO: parse the URL for issue_id and event_id
    # e.g. https://mozilla.sentry.io/issues/7325411784/events/16f37d8003bb42f4abff707fb57bfca0/?alert=5775&detection_type=static&notification_uuid=a672e1f5-d561-4f2d-92f4-441dd86e8fd2&openPeriod=589904722&project=4510958917713920&referrer=metric_alert_slack&statsPeriod=12d

    # return issue_id, event_id
    return "7325411784", "16f37d8003bb42f4abff707fb57bfca0"


async def run_alert_triage(
    *,
    sentry_mcp_server: McpServerConfig,
    sentry_alert_url: str,
    rules_dir: Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    effort: str | None = None,
    verbose: bool = False,
    log: Path | None = None,
    actions_recorder: ActionsRecorder | None = None
) -> SentryTriageResult:
    """Triage a single Sentry alert with a claude-agent-sdk agent.

    Returns a :class:`SentryTriageResult` on success; raises :class:`AgentError` if the
    agent ends in an error.
    """

    # load custom claude rules
    if rules_dir is None:
        rules_dir = HERE / "rules"

    print(f"[sentry_triage] triaging sentry alert at {sentry_alert_url}", file=sys.stderr)

    # Action-recording MCP server (in-process). Standalone/script runs pass
    # actions_recorder=None and get a local recorder that copies attachments
    # under ./artifacts (no uploader).
    actions_recorder, actions_server = actions_server_for(
        actions_recorder, types=TRIAGE_ACTIONS
    )

    sentry_issue_id, sentry_event_id = parse_sentry_alert_url_for_issue_and_event_ids(sentry_alert_url)

    enabled_action_tools = actions_to_tool_names(TRIAGE_ACTIONS)

    system_prompt = render_prompt("system.md", rules_dir=str(rules_dir.resolve()))
    user_prompt = render_prompt("triage.md", issue_id=sentry_issue_id, event_id=sentry_event_id, rules_path=rules_dir)

    options = ClaudeAgentOptions(
        add_dirs=[str(rules_dir.resolve())],
        system_prompt=system_prompt,
        mcp_servers={
            "sentry": sentry_mcp_server,
            ACTIONS_SERVER_NAME: actions_server,
        },
        agents={"investigator": make_alert_investigator()},
        permission_mode="bypassPermissions",
        allowed_tools=[
            "Read",
            "Grep",
            "Glob",
            "Bash",
            "Task",
            *enabled_action_tools,
            *SENTRY_READ_TOOLS,
        ],
        model=model,
        max_turns=max_turns,
        **({"effort": effort} if effort else {}),
        setting_sources=[],
    )

    result_msg: ResultMessage | None = None

    with Reporter(verbose=verbose, log_path=log) as reporter:
        reporter.header(f"sentry alert url {sentry_alert_url}")

        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_prompt)

            async for msg in client.receive_response():
                reporter.message(msg)

                if isinstance(msg, ResultMessage):
                    result_msg = msg

    if result_msg is None:
        raise AgentError(f"sentry alert url {sentry_alert_url}: agent produced no result message")

    if result_msg.is_error:
        raise AgentError(
            f"sentry alert url {sentry_alert_url} triage failed: {result_msg.result or result_msg.subtype}"
        )

    return SentryTriageResult(
        result=result_msg.result,
        num_turns=result_msg.num_turns,
        total_cost_usd=result_msg.total_cost_usd,
    )
