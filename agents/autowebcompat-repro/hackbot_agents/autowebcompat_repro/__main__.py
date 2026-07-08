import logging
from datetime import datetime

from hackbot_runtime import (
    HackbotAgentResult,
    HackbotContext,
    run_async,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import (
    AutowebcompatReproResult,
    BugDataInput,
    BugIdInput,
    RunTracker,
    TaskConfig,
    run_autowebcompat_repro,
)

logger = logging.getLogger("autowebcompat-repro")


class AgentInputs(BaseSettings):
    bugzilla_mcp_url: str
    bug_data: str | None = None
    bug_id: int | None = None
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(extra="ignore")


class AutowebcompatResult(HackbotAgentResult):
    result: AutowebcompatReproResult
    start_time: datetime
    end_time: datetime


async def main(ctx: HackbotContext) -> AutowebcompatResult:
    start_time = datetime.now()
    inputs = AgentInputs()  # type: ignore

    if inputs.bug_data is not None:
        input_data = BugDataInput(bug_data=inputs.bug_data)
    elif inputs.bug_id is not None:
        input_data = BugIdInput(bug_id=inputs.bug_id)

    tracker = RunTracker()
    result = await run_autowebcompat_repro(
        TaskConfig(
            model=inputs.model,
            max_turns=inputs.max_turns,
            effort=inputs.effort,
            log=ctx.log_path,
            verbose=True,
        ),
        tracker,
        input_data,
        bugzilla_mcp_server={
            "type": "http",
            "url": inputs.bugzilla_mcp_url,
        },
        publish_file=ctx.publish_file,
    )
    end_time = datetime.now()

    result = AutowebcompatResult(
        result=result,
        num_turns=tracker.num_turns,
        total_cost_usd=tracker.total_cost_usd,
        start_time=start_time,
        end_time=end_time,
    )
    logger.info("Run completed with result: %s", result)
    return result


if __name__ == "__main__":
    run_async(main)
