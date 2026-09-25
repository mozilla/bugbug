from hackbot_runtime import HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import SentryTriageResult, run_alert_triage


class AgentInputs(BaseSettings):
    sentry_alert_url: str
    sentry_mcp_url: str
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None
    
    model_config = SettingsConfigDict(extra="ignore")


async def main(ctx: HackbotContext) -> SentryTriageResult:
    inputs = AgentInputs()

    return await run_alert_triage(
        sentry_mcp_server={
            "type": "http",
            "url": inputs.sentry_mcp_url,
        },
        sentry_alert_url=inputs.sentry_alert_url,
        model=inputs.model,
        max_turns=inputs.max_turns,
        effort=inputs.effort,
        log=ctx.log_path,
        verbose=True,
        actions_recorder=ctx.actions,
    )


if __name__ == "__main__":
    run_async(main)
