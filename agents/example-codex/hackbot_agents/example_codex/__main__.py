from hackbot_runtime import HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import ExampleCodexResult, run_example_codex


class AgentInputs(BaseSettings):
    bug_id: int
    model: str | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(extra="ignore")


async def main(ctx: HackbotContext) -> ExampleCodexResult:
    inputs = AgentInputs()
    return await run_example_codex(
        bug_id=inputs.bug_id,
        model=inputs.model,
        effort=inputs.effort,
        log=ctx.log_path,
        verbose=True,
        actions_recorder=ctx.actions,
    )


if __name__ == "__main__":
    run_async(main)
