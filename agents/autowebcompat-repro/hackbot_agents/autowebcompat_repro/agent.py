"""Firefox web-compatibility reproduction agent.

Drives an agent that reproduces a broken-site report in Firefox
using the Firefox DevTools MCP. The bug is passed either inline as ``bug_data``
text or a Bugzilla ``bug_id`` (read via Bugzilla broker).
"""

from __future__ import annotations

import logging
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpServerConfig,
    ResultMessage,
)
from hackbot_runtime import AgentError, HackbotAgentResult
from hackbot_runtime.claude import Reporter

from .config import BUGZILLA_READ_TOOLS, DEVTOOLS_TOOLS
from .devtools_mcp import build_devtools_server
from .result import (
    RESULT_SERVER_NAME,
    SUBMIT_RESULT_TOOL,
    ReproductionResult,
    ResultCollector,
    build_result_server,
)

HERE = Path(__file__).resolve().parent

logger = logging.getLogger("autowebcompat-repro")


class AutowebcompatReproResult(HackbotAgentResult):
    result: ReproductionResult | None = None


def load_system_prompt() -> str:
    return (HERE / "prompts" / "system.md").read_text()


def build_user_prompt(bug_data: str | None, bug_id: int | None) -> str:
    if bug_data:
        return (
            "Here is the web-compatibility report to work on:\n\n"
            f"{bug_data}\n\n"
            "Follow your task procedure."
        )
    if bug_id is not None:
        return (
            f"The web-compatibility report to work on is Bugzilla bug {bug_id}. "
            "Fetch it using the Bugzilla MCP tools, then follow your task procedure."
        )
    raise AgentError("neither bug_data nor bug_id was provided")


async def run_autowebcompat_repro(
    *,
    bugzilla_mcp_server: McpServerConfig,
    bug_data: str | None = None,
    bug_id: int | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    effort: str | None = None,
    firefox_path: str | None = None,
    chrome_mask_profile: Path | None = None,
    verbose: bool = False,
    log: Path | None = None,
) -> AutowebcompatReproResult:
    """Reproduce a web-compat issue and return the agent's findings.

    Returns a :class:`AutowebcompatReproResult` on success; raises
    :class:`AgentError` if the agent ends in an error.
    """
    subject = bug_data if bug_data else f"bug {bug_id}"
    preview = subject if len(subject) <= 200 else f"{subject[:200]}..."
    logger.info("reproducing %s", preview)

    devtools_server = build_devtools_server(
        firefox_path=Path(firefox_path) if firefox_path else None,
        headless=True,
        enable_script=True,
        enable_privileged_context=chrome_mask_profile is not None,
        profile_path=chrome_mask_profile,
    )

    # Structured-result MCP server (in-process): the agent calls submit_result
    # once at the end, giving a predictable JSON result instead of free text.
    result_collector = ResultCollector()
    result_server = build_result_server(result_collector)

    # Only wire up Bugzilla when there's a bug to fetch. With inline bug_data
    # there's nothing to read, so the bugzilla MCP is not available
    mcp_servers: dict[str, McpServerConfig] = {
        "firefox-devtools": devtools_server,
        RESULT_SERVER_NAME: result_server,
    }
    bugzilla_tools: list[str] = []
    if bug_id is not None:
        mcp_servers["bugzilla"] = bugzilla_mcp_server
        bugzilla_tools = BUGZILLA_READ_TOOLS

    system_prompt = load_system_prompt()

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        permission_mode="bypassPermissions",
        allowed_tools=[
            "Read",
            "Grep",
            "Glob",
            "Bash",
            *bugzilla_tools,
            *DEVTOOLS_TOOLS,
            SUBMIT_RESULT_TOOL,
        ],
        model=model,
        max_turns=max_turns,
        **({"effort": effort} if effort else {}),
        setting_sources=[],
        # DevTools snapshots/screenshots of complex pages serialize to JSON that
        # can exceed the SDK's default 1 MiB message buffer (the reader dies
        # fatally if it does). Raise it well above that ceiling.
        max_buffer_size=10 * 1024 * 1024,
    )

    user_prompt = build_user_prompt(bug_data, bug_id)

    result_msg: ResultMessage | None = None
    with Reporter(verbose=verbose, log_path=log, max_turns=max_turns) as reporter:
        reporter.header(subject)
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_prompt)
            async for msg in client.receive_response():
                reporter.message(msg)
                if isinstance(msg, ResultMessage):
                    result_msg = msg

    if result_msg is None:
        raise AgentError(f"{subject}: agent produced no result message")
    if result_msg.is_error:
        raise AgentError(
            f"{subject} investigation failed: {result_msg.result or result_msg.subtype}"
        )
    if result_collector.result is None:
        raise AgentError(
            f"{subject}: agent finished without submitting a result via submit_result"
        )

    return AutowebcompatReproResult(
        result=result_collector.result,
        num_turns=result_msg.num_turns,
        total_cost_usd=result_msg.total_cost_usd,
    )
