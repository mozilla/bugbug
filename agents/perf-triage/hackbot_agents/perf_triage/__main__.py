import logging

from hackbot_runtime import HackbotContext, run_async
from performance_tools.inputs import BugIdInput
from performance_tools.tasks import RunTracker, TaskConfig
from performance_tools.triage import PerfTriage
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import PerfTriageFindings, run_perf_triage

logger = logging.getLogger("perf-triage")


class AgentInputs(BaseSettings):
    # Every field here MUST also appear in compose.yml's environment list, or
    # Docker drops it and the default silently applies (tests/test_compose_env.py).
    broker_url: str
    bug_id: int
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(extra="ignore")

    @property
    def bugzilla_mcp_url(self) -> str:
        return f"{self.broker_url.rstrip('/')}/mcp"


async def main(ctx: HackbotContext) -> PerfTriageFindings:
    inputs = AgentInputs()  # type: ignore[call-arg]
    server = {"type": "http", "url": inputs.bugzilla_mcp_url}
    tracker = RunTracker()
    config = TaskConfig(
        model=inputs.model,
        max_turns=inputs.max_turns,
        effort=inputs.effort,
        log=ctx.log_path,
        verbose=True,
    )

    async def triage(profile_urls: list[str]):
        return await PerfTriage(
            config,
            tracker,
            BugIdInput(bug_id=inputs.bug_id),
            server,
            profile_urls=profile_urls,
            analyse_profile=bool(profile_urls),
        ).run()

    findings = await run_perf_triage(
        bug_id=inputs.bug_id,
        bugzilla_mcp_server=server,
        recorder=ctx.actions,
        triage=triage,
        num_turns=lambda: tracker.num_turns,
        total_cost_usd=lambda: tracker.total_cost_usd,
    )
    logger.info(
        "perf-triage %s: %s",
        inputs.bug_id,
        findings.model_dump_json(exclude={"triage"}),
    )
    return findings


if __name__ == "__main__":
    run_async(main)
