from hackbot_runtime import HackbotContext, run_async
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import RegressionRangeResult, run_regression_range


class AgentInputs(BaseSettings):
    bug_id: int
    bugzilla_mcp_url: str
    # Optional bisection bounds (date, version number, or changeset). When
    # omitted, the agent infers them from the bug.
    good: str | None = None
    bad: str | None = None
    model: str | None = None
    # Model the nested mozregression --prompt agent uses; defaults to `model`.
    mozregression_model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(extra="ignore")

    @field_validator(
        "good", "bad", "model", "mozregression_model", "effort", mode="before"
    )
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        # compose passes optional inputs as empty strings when unset; treat those
        # as absent so the agent infers bounds / uses defaults.
        if isinstance(v, str) and not v.strip():
            return None
        return v


async def main(ctx: HackbotContext) -> RegressionRangeResult:
    inputs = AgentInputs()

    return await run_regression_range(
        bugzilla_mcp_server={
            "type": "http",
            "url": inputs.bugzilla_mcp_url,
        },
        bug=inputs.bug_id,
        anthropic_api_key=ctx.anthropic.api_key,
        good=inputs.good,
        bad=inputs.bad,
        model=inputs.model,
        mozregression_model=inputs.mozregression_model,
        max_turns=inputs.max_turns,
        effort=inputs.effort,
        log=ctx.log_path,
        verbose=True,
        actions_recorder=ctx.actions,
    )


if __name__ == "__main__":
    run_async(main)
