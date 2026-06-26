from hackbot_runtime import HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import TestPlanGeneratorResult, run_test_plan_generator
from .firefox_install import install_firefox_nightly


class AgentInputs(BaseSettings):
    feature_name: str
    feature_description: str
    test_scope: str
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(extra="ignore")


async def main(ctx: HackbotContext) -> TestPlanGeneratorResult:
    inputs = AgentInputs()

    firefox_path = str(install_firefox_nightly())

    return await run_test_plan_generator(
        feature_name=inputs.feature_name,
        feature_description=inputs.feature_description,
        test_scope=inputs.test_scope,
        model=inputs.model,
        max_turns=inputs.max_turns,
        effort=inputs.effort,
        firefox_path=firefox_path,
        log=ctx.log_path,
        verbose=True,
    )


if __name__ == "__main__":
    run_async(main)
