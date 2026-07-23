"""Firefox QA test-plan generator and executor."""

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

from .config import DEVTOOLS_TOOLS
from .devtools_mcp import build_devtools_server
from .result import (
    RESULT_SERVER_NAME,
    SUBMIT_RESULT_TOOL,
    ResultCollector,
    TestPlanResult,
    build_result_server,
)

HERE = Path(__file__).resolve().parent

logger = logging.getLogger("test-plan-generator")


class TestPlanGeneratorResult(HackbotAgentResult):
    result: TestPlanResult | None = None


def load_system_prompt() -> str:
    return (HERE / "prompts" / "system.md").read_text()


def build_user_prompt(
    feature_name: str, feature_description: str, test_scope: str
) -> str:
    return (
        "Generate and run a Firefox QA test plan from these inputs.\n\n"
        f"Feature name:\n{feature_name}\n\n"
        f"Feature description:\n{feature_description}\n\n"
        f"Test scope:\n{test_scope}\n\n"
        "Use the provided feature name as the structured result feature. "
        "Keep all generated test cases within the provided test scope.\n\n"
        "Follow the required workflow exactly: before execution, generate no more than 30 test cases to cover all distinct behaviors, meaningful variations, and "
        "negative scenarios. Run the cases in order, stop each case after its first "
        "failed step, and submit exactly one structured result."
    )


async def run_test_plan_generator(
    *,
    feature_name: str,
    feature_description: str,
    test_scope: str,
    model: str | None = None,
    max_turns: int | None = None,
    effort: str | None = None,
    firefox_path: str | None = None,
    verbose: bool = False,
    log: Path | None = None,
) -> TestPlanGeneratorResult:
    """Generate and run a Firefox QA test plan for one feature."""
    subject = feature_name
    logger.info("generating Firefox QA test plan for %s", subject)

    devtools_server = build_devtools_server(
        firefox_path=Path(firefox_path) if firefox_path else None,
        headless=True,
        enable_script=True,
    )

    result_collector = ResultCollector()
    result_server = build_result_server(result_collector)

    mcp_servers: dict[str, McpServerConfig] = {
        "firefox-devtools": devtools_server,
        RESULT_SERVER_NAME: result_server,
    }

    options = ClaudeAgentOptions(
        system_prompt=load_system_prompt(),
        mcp_servers=mcp_servers,
        permission_mode="bypassPermissions",
        allowed_tools=[
            *DEVTOOLS_TOOLS,
            SUBMIT_RESULT_TOOL,
        ],
        model=model,
        max_turns=max_turns,
        **({"effort": effort} if effort else {}),
        setting_sources=[],
        max_buffer_size=10 * 1024 * 1024,
    )

    result_msg: ResultMessage | None = None
    with Reporter(verbose=verbose, log_path=log) as reporter:
        reporter.header(subject)
        async with ClaudeSDKClient(options=options) as client:
            await client.query(
                build_user_prompt(feature_name, feature_description, test_scope)
            )
            async for msg in client.receive_response():
                reporter.message(msg)
                if isinstance(msg, ResultMessage):
                    result_msg = msg

    if result_msg is None:
        raise AgentError(f"{subject}: agent produced no result message")
    if result_msg.is_error:
        raise AgentError(
            f"{subject} test-plan generation failed: "
            f"{result_msg.result or result_msg.subtype}"
        )
    if result_collector.result is None:
        raise AgentError(
            f"{subject}: agent finished without submitting a result via submit_result"
        )

    return TestPlanGeneratorResult(
        result=result_collector.result,
        num_turns=result_msg.num_turns,
        total_cost_usd=result_msg.total_cost_usd,
    )
