import logging
from datetime import datetime
from typing import Literal

from autowebcompat_tools.inputs import BugDataInput, BugIdInput
from autowebcompat_tools.task import RunTracker, TaskConfig
from hackbot_runtime import (
    HackbotAgentResult,
    HackbotContext,
    checkout_revision,
    run_async,
)
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import AutowebcompatInterventionResult, run_autowebcompat_intervention
from .config import CHECKOUT_DEPTH, PHABRICATOR_READ_TOOLS

logger = logging.getLogger("autowebcompat-intervention")


class AgentInputs(BaseSettings):
    broker_url: str
    bug_data: str | None = None
    bug_id: int | None = None
    # Set when following up on a revision this agent filed: the revision to
    # update and the comment that mentioned Hackbot.
    revision_id: int | None = None
    comment: str | None = None
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
    def require_subject(self) -> AgentInputs:
        if self.bug_data is None and self.bug_id is None:
            raise ValueError("provide at least one of bug_data (BUG_DATA) or bug_id")
        return self

    @model_validator(mode="after")
    def require_follow_up_context(self) -> "AgentInputs":
        if self.revision_id is not None:
            if not self.comment:
                raise ValueError(
                    "comment (COMMENT) is required when revision_id is set, to "
                    "respond on the revision"
                )
            if self.bug_id is None:
                raise ValueError("bug_id (BUG_ID) is required when revision_id is set")
        elif self.comment is not None:
            raise ValueError("comment (COMMENT) requires revision_id")
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

    if inputs.revision_id is not None:
        await checkout_revision(
            ctx, inputs.revision_id, inputs.broker_url, depth=CHECKOUT_DEPTH
        )
    else:
        await ctx.prepare_repo(depth=CHECKOUT_DEPTH)

    if inputs.bug_data is not None:
        input_data: BugDataInput | BugIdInput = BugDataInput(bug_data=inputs.bug_data)
    else:
        assert inputs.bug_id is not None
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
        revision_id=inputs.revision_id,
        comment=inputs.comment,
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
