from hackbot_runtime import HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import BugFixResult, run_bug_fix


class AgentInputs(BaseSettings):
    bug_id: int
    bugzilla_mcp_url: str
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(extra="ignore")


async def main(ctx: HackbotContext) -> BugFixResult:
    inputs = AgentInputs()

    return await run_bug_fix(
        task="Triage and fix the bug, and verify the fix",
        bugzilla_mcp_server={
            "type": "http",
            "url": inputs.bugzilla_mcp_url,
        },
        source_repo=ctx.source_repo,
        fx_ctx=ctx.firefox,
        bug=inputs.bug_id,
        model=inputs.model,
        max_turns=inputs.max_turns,
        effort=inputs.effort,
        log=ctx.log_path,
        verbose=True,
        actions_recorder=ctx.actions,
    )


if __name__ == "__main__":
    run_async(main)
