import sys

from hackbot_runtime import HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import FrontendTriageResult, run_frontend_triage
from .notify import record_notification
from .preflight import attached_fix, fetch_bug

TRIAGE_TASK = (
    "Triage this user-facing Firefox bug. Investigate the source tree "
    "READ-ONLY (Read/Grep/Glob/Bash) to determine the likely root cause, then "
    "produce a concrete proposed fix plan: the target files and the approach. "
    "Do NOT build, run, or modify the source, and do NOT attempt to reproduce "
    "the bug by running Firefox. Record your findings and plan as a single brief "
    "Bugzilla comment."
)


DEFAULT_MODEL = "claude-opus-5"


class AgentInputs(BaseSettings):
    bug_id: int
    broker_url: str
    model: str = DEFAULT_MODEL
    max_turns: int | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(extra="ignore")

    @property
    def bugzilla_mcp_url(self) -> str:
        return f"{self.broker_url.rstrip('/')}/mcp"


async def main(ctx: HackbotContext) -> FrontendTriageResult:
    inputs = AgentInputs()
    bugzilla_mcp_server = {"type": "http", "url": inputs.bugzilla_mcp_url}

    # Ahead of `prepare_repo`: a bug already being worked on should cost neither
    # the mozilla-central checkout nor a model turn.
    bug_fields = await fetch_bug(bugzilla_mcp_server, inputs.bug_id)
    reason = attached_fix(bug_fields)
    if reason:
        print(
            f"[frontend_triage] skipping bug {inputs.bug_id}: {reason}",
            file=sys.stderr,
        )
        # Returned, not raised: `_finish` turns an exception into
        # `status: "error"`, and this run did what it should have.
        return FrontendTriageResult(
            bug_id=inputs.bug_id,
            num_turns=0,
            total_cost_usd=0.0,
            product=bug_fields.get("product"),
            component=bug_fields.get("component"),
            actionable=False,
            result=f"Skipped bug {inputs.bug_id}: {reason}. No triage was run.",
        )

    await ctx.prepare_repo()

    result = await run_frontend_triage(
        task=TRIAGE_TASK,
        bugzilla_mcp_server=bugzilla_mcp_server,
        source_repo=ctx.repo_path,
        bug=inputs.bug_id,
        model=inputs.model,
        max_turns=inputs.max_turns,
        effort=inputs.effort,
        log=ctx.log_path,
        verbose=True,
        actions_recorder=ctx.actions,
    )

    # Recorded last, so it applies after the Bugzilla writes it reports. Only an
    # auto-applied run in a component with a channel records anything -- see notify.py.
    record_notification(ctx.actions, result, run_id=ctx.run_id)
    return result


if __name__ == "__main__":
    run_async(main)
