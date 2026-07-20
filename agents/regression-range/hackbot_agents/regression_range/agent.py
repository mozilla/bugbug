"""Regression-range finder -- an agent that bisects Firefox regressions.

Orchestrates a Claude agent that, given a Bugzilla regression bug, works out
good/bad bounds and a natural-language reproduction directive, then runs
``mozregression`` in ``--prompt`` mode (mozilla/mozregression#2197) to bisect the
regression and report the resulting changeset/pushlog range. It records a
Bugzilla comment with the range and, at high confidence, proposes field updates
(regressed_by / cf_has_regression_range / clearing regressionwindow-wanted).

Bugzilla is reached via an out-of-process MCP broker (HTTP transport) that holds
the Bugzilla token -- the agent process itself never sees it. HGMO and Searchfox
are public and run in-process. The mozregression tool shells out to the CLI,
which drives its own headless Firefox + DevTools MCP to classify each build.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

from agent_tools import mozilla_vcs, mozregression, searchfox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.mozilla_vcs import MozillaVcsContext
from agent_tools.mozregression import MozregressionContext
from agent_tools.searchfox import SearchfoxContext
from claude_agent_sdk import (
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
    MOZREGRESSION_TOOLS,
    SEARCHFOX_TOOLS,
)

HERE = Path(__file__).resolve().parent

# The agent ends its final message with a fenced ```json block carrying the
# structured result; we parse the last such block so it is machine-consumable.
_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


class RegressionRangeResult(HackbotAgentResult):
    bug_id: int
    # One of: range_found | inconclusive | not_automatable | not_a_regression
    status: str | None = None
    pushlog_url: str | None = None
    first_bad_changeset: str | None = None
    last_good_changeset: str | None = None
    # hg node when the range was narrowed to a single introducing changeset.
    regressor_node: str | None = None
    # Bug number that landed the regressor, when identified.
    regressed_by_bug: int | None = None
    good_bound: str | None = None
    bad_bound: str | None = None
    prompt_used: str | None = None
    confidence: str | None = None
    summary: str | None = None
    # The agent's full final message, always present as a fallback.
    result: str | None = None


def load_system_prompt(extra: str) -> str:
    tmpl = (HERE / "prompts" / "system.md").read_text()
    return tmpl.format(extra_instructions=extra or "(none)")


def parse_result(text: str | None) -> dict:
    """Extract the structured result from the agent's final message, if present.

    Returns an empty dict when no parseable ```json block is found -- the raw
    text is still preserved in ``RegressionRangeResult.result``.
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

    def _as_int(value):
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    return {
        "status": data.get("status"),
        "pushlog_url": data.get("pushlog_url"),
        "first_bad_changeset": data.get("first_bad_changeset"),
        "last_good_changeset": data.get("last_good_changeset"),
        "regressor_node": data.get("regressor_node"),
        "regressed_by_bug": _as_int(data.get("regressed_by_bug")),
        "good_bound": data.get("good_bound"),
        "bad_bound": data.get("bad_bound"),
        "prompt_used": data.get("prompt_used"),
        "confidence": data.get("confidence"),
        "summary": data.get("summary"),
    }


async def run_regression_range(
    *,
    bugzilla_mcp_server: McpServerConfig,
    bug: int,
    anthropic_api_key: str | None = None,
    good: str | None = None,
    bad: str | None = None,
    instructions: str = "",
    model: str | None = None,
    mozregression_model: str | None = None,
    max_turns: int | None = None,
    effort: str | None = None,
    verbose: bool = False,
    log: Path | None = None,
    actions_recorder: ActionsRecorder | None = None,
) -> RegressionRangeResult:
    """Bisect a single Bugzilla regression bug with mozregression.

    Returns a :class:`RegressionRangeResult` on success; raises
    :class:`AgentError` if the agent ends in an error.
    """
    print(f"[regression_range] bisecting bug {bug}", file=sys.stderr)

    # Action-recording MCP server (in-process). Standalone/script runs pass
    # actions_recorder=None and get a local recorder (no uploader).
    actions_recorder, actions_server = actions_server_for(
        actions_recorder, types=ENABLED_ACTION_TYPES
    )
    enabled_action_tools = actions_to_tool_names(ENABLED_ACTION_TYPES)

    # In-process MCP servers. Searchfox and HGMO are public (no credentials).
    searchfox_server = build_sdk_server(
        "searchfox", SearchfoxContext(client=AsyncSearchfoxClient()), searchfox.TOOLS
    )
    vcs_server = build_sdk_server("mozilla_vcs", MozillaVcsContext(), mozilla_vcs.TOOLS)
    mozregression_server = build_sdk_server(
        "mozregression",
        MozregressionContext(
            anthropic_api_key=anthropic_api_key,
            default_model=mozregression_model or model,
        ),
        mozregression.TOOLS,
    )

    system_prompt = load_system_prompt(instructions)

    # No source checkout is needed; give the agent a scratch cwd for the CLI.
    workdir = Path(tempfile.mkdtemp(prefix="regression-range-"))

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={
            "bugzilla": bugzilla_mcp_server,
            "searchfox": searchfox_server,
            "mozilla_vcs": vcs_server,
            "mozregression": mozregression_server,
            ACTIONS_SERVER_NAME: actions_server,
        },
        cwd=str(workdir),
        permission_mode="bypassPermissions",
        allowed_tools=[
            "Read",
            "Grep",
            "Glob",
            "Bash",
            *BUGZILLA_READ_TOOLS,
            *SEARCHFOX_TOOLS,
            *MOZILLA_VCS_TOOLS,
            *MOZREGRESSION_TOOLS,
            *enabled_action_tools,
        ],
        model=model,
        max_turns=max_turns,
        **({"effort": effort} if effort else {}),
        setting_sources=[],
    )

    bounds_hint = ""
    if good or bad:
        bounds_hint = (
            "\n\nBounds provided for this run (use them unless the bug clearly "
            f"contradicts them): good={good or 'unspecified'}, "
            f"bad={bad or 'unspecified'}."
        )
    user_prompt = (
        f"Find the regression range for Bugzilla bug {bug}.\n\n"
        "Fetch the bug with the bugzilla MCP tools, decide whether it is an "
        "automatable regression, determine good/bad bounds, then run "
        "mozregression to bisect it. Follow your system instructions." + bounds_hint
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
            f"bug {bug} regression-range failed: "
            f"{result_msg.result or result_msg.subtype}"
        )

    parsed = parse_result(result_msg.result)

    return RegressionRangeResult(
        bug_id=bug,
        result=result_msg.result,
        num_turns=result_msg.num_turns,
        total_cost_usd=result_msg.total_cost_usd,
        **parsed,
    )
