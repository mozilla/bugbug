from hackbot_runtime import HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import FrontendTriageResult, run_frontend_triage

TRIAGE_TASK = (
    "Triage this Firefox desktop frontend bug. Investigate the source tree "
    "READ-ONLY (Read/Grep/Glob/Bash) to determine the likely root cause, then "
    "produce a concrete proposed fix plan: the target files and the approach. "
    "Do NOT build, run, or modify the source, and do NOT attempt to reproduce "
    "the bug by running Firefox. Record your findings and plan as a single brief "
    "Bugzilla comment."
)


class AgentInputs(BaseSettings):
    bug_id: int
    bugzilla_mcp_url: str
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(extra="ignore")


async def main(ctx: HackbotContext) -> FrontendTriageResult:
    inputs = AgentInputs()

    return await run_frontend_triage(
        task=TRIAGE_TASK,
        bugzilla_mcp_server={
            "type": "http",
            "url": inputs.bugzilla_mcp_url,
        },
        source_repo=ctx.source_repo,
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
