import logging
from datetime import datetime
from typing import Literal

from autowebcompat_tools.task import RunTracker, TaskConfig
from hackbot_runtime import (
    HackbotAgentResult,
    HackbotContext,
    run_async,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import run_autowebcompat_etp_compare
from .result import EtpComparisonResult

logger = logging.getLogger("autowebcompat-etp-compare")


class AgentInputs(BaseSettings):
    url: str
    model: str | None = None
    max_turns: int | None = None
    effort: (
        Literal["low"]
        | Literal["medium"]
        | Literal["high"]
        | Literal["xhigh"]
        | Literal["max"]
        | None
    ) = None
    headless: bool = False

    model_config = SettingsConfigDict(extra="ignore")


class AutowebcompatEtpCompareResult(HackbotAgentResult):
    result: EtpComparisonResult
    start_time: datetime
    end_time: datetime


async def main(ctx: HackbotContext) -> AutowebcompatEtpCompareResult:
    start_time = datetime.now()
    inputs = AgentInputs()  # type: ignore

    tracker = RunTracker()
    result = await run_autowebcompat_etp_compare(
        TaskConfig(
            model=inputs.model,
            max_turns=inputs.max_turns,
            effort=inputs.effort,
            log=ctx.log_path,
            verbose=True,
            headless=inputs.headless,
        ),
        tracker,
        inputs.url,
        publish_file=ctx.publish_file,
    )
    end_time = datetime.now()

    result = AutowebcompatEtpCompareResult(
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
