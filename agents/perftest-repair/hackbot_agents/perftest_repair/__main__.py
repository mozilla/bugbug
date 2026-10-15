import tempfile
from pathlib import Path

from hackbot_runtime import HackbotContext, run_async
from hackbot_runtime.actions.slack import record_message
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import PerftestRepairResult, run_perftest_repair
from .config import SKIP_FIREFOX_BUILD, SLACK_CHANNEL, SLACK_REF
from .notify import build_message, revision_params, try_push_of


class AgentInputs(BaseSettings):
    # An intermittent or permanent failure bug filed against a perf test job.
    bug_id: int
    broker_url: str
    # When set, a proposed patch is not built or run, and is reported as unverified.
    skip_firefox_build: bool = SKIP_FIREFOX_BUILD
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None
    # Local testing only: investigate a bug even if it is closed or owned, to
    # compare the diagnosis with the fix that landed. Not in hackbot-api's input
    # schema, so no trigger or UI run can set it.
    ignore_stop_conditions: bool = False

    # Compose passes unset per-run inputs as empty strings; treat those as absent.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)

    def broker_endpoint(self, path: str) -> str:
        return f"{self.broker_url.rstrip('/')}/{path.lstrip('/')}"

    @property
    def bugzilla_mcp_url(self) -> str:
        return self.broker_endpoint("/bugzilla/mcp")


async def main(ctx: HackbotContext) -> PerftestRepairResult:
    inputs = AgentInputs()
    await ctx.prepare_repo()

    scratch_out = Path(tempfile.mkdtemp(prefix="perftest-repair-")) / "out"
    scratch_out.mkdir(parents=True, exist_ok=True)

    result = await run_perftest_repair(
        bugzilla_mcp_server={"type": "http", "url": inputs.bugzilla_mcp_url},
        source_repo=ctx.repo_path,
        fx_ctx=ctx.firefox,
        bug=inputs.bug_id,
        scratch_out=scratch_out,
        skip_firefox_build=inputs.skip_firefox_build,
        model=inputs.model,
        max_turns=inputs.max_turns,
        effort=inputs.effort,
        log=ctx.log_path,
        verbose=True,
        actions_recorder=ctx.actions,
        ignore_stop_conditions=inputs.ignore_stop_conditions,
        publish_file=ctx.publish_file,
    )
    # A skipped bug had no recent failures: nothing to report until it fails again.
    if result.outcome == "skipped":
        return result
    try_push = try_push_of(ctx.actions) if result.outcome == "fix" else None
    message = build_message(result, run_id=ctx.run_id, try_push=try_push)
    record_message(ctx.actions, SLACK_CHANNEL, message, ref=SLACK_REF)
    if try_push:
        ctx.actions.record(
            "phabricator.submit_patch",
            revision_params(result, try_push),
            reasoning="Open the fix for review once its try push passes.",
            ref="revision",
        )
    return result


if __name__ == "__main__":
    run_async(main)
