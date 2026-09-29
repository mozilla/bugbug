import logging
from datetime import datetime
from typing import Literal

from hackbot_runtime import (
    HackbotAgentResult,
    HackbotContext,
    run_async,
)
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import (
    AutowebcompatInterventionResult,
    BugDataInput,
    BugIdInput,
    RunTracker,
    TaskConfig,
    run_autowebcompat_intervention,
)
from .config import PHABRICATOR_READ_TOOLS

logger = logging.getLogger("autowebcompat-intervention")


class AgentInputs(BaseSettings):
    broker_url: str
    bug_data: str | None = None
    bug_id: int | None = None
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

    @model_validator(mode="after")
    def require_subject(self) -> "AgentInputs":
        if self.bug_data is None and self.bug_id is None:
            raise ValueError("provide at least one of bug_data (BUG_DATA) or bug_id")
        return self

    @property
    def bugzilla_mcp_url(self) -> str:
        return f"{self.broker_url.rstrip('/')}/bugzilla/mcp"

    @property
    def phabricator_mcp_url(self) -> str:
        return f"{self.broker_url.rstrip('/')}/phabricator/mcp"


class AutowebcompatResult(HackbotAgentResult):
    result: AutowebcompatInterventionResult
    start_time: datetime
    end_time: datetime


async def main(ctx: HackbotContext) -> AutowebcompatResult:
    start_time = datetime.now()
    inputs = AgentInputs()  # type: ignore

    await ctx.prepare_repo()

    if inputs.bug_data is not None:
        input_data: BugDataInput | BugIdInput = BugDataInput(bug_data=inputs.bug_data)
    else:
        assert inputs.bug_id is not None  # guaranteed by require_subject
        input_data = BugIdInput(bug_id=inputs.bug_id)

    tracker = RunTracker()
    result = await run_autowebcompat_intervention(
        TaskConfig(
            model=inputs.model,
            max_turns=inputs.max_turns,
            effort=inputs.effort,
            log=ctx.log_path,
            verbose=True,
            headless=inputs.headless,
        ),
        tracker,
        input_data,
        source_repo=ctx.repo_path,
        fx_ctx=ctx.firefox,
        bugzilla_mcp_server={"type": "http", "url": inputs.bugzilla_mcp_url},
        phabricator_mcp_server={"type": "http", "url": inputs.phabricator_mcp_url},
        phabricator_read_tools=PHABRICATOR_READ_TOOLS,
        actions_recorder=ctx.actions,
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
