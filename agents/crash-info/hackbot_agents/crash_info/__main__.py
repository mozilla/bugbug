"""Hackbot entry point: ``python -m hackbot_agents.crash_info``.

Inputs come from the environment (``bug_id`` -> ``BUG_ID``). The agent is
read-only: it publishes its report, its notes for bug-fix and its state as
artifacts, and changes nothing in Bugzilla.
"""

import sys
from datetime import datetime

from hackbot_runtime import AgentError, HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import DEFAULT_EFFORT, DEFAULT_MODEL, CrashInfoResult, run_crash_info
from .report import render_handoff
from .settings import load_settings

STATE_ARTIFACT = "crash_info/state.json"
REPORT_ARTIFACT = "crash_info/report.json"
MARKDOWN_ARTIFACT = "crash_info/report.md"
# What a chained bug-fix run reads instead of the full report: all the missing
# information CrashInfo found, unfiltered, and what stays unknown.
HANDOFF_ARTIFACT = "crash_info/handoff.md"


class AgentInputs(BaseSettings):
    bug_id: int
    # Historical (evaluation) runs: hide everything after this time, or after
    # the bug's first patch. Neither: a live run on the bug as it is now.
    as_of: datetime | None = None
    before_first_patch: bool = False
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    max_tool_calls: int | None = None
    # Search the Firefox checkout Hackbot prepares instead of Searchfox (live runs only:
    # the shallow checkout is at the current tip, which would leak into as-of runs).
    use_source_checkout: bool = False

    # Compose passes unset per-run inputs as empty strings; treat those as absent.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)


async def main(ctx: HackbotContext) -> CrashInfoResult:
    inputs = AgentInputs()
    # Fails here, with a clear message, when no Anthropic key is set.
    _ = ctx.anthropic.api_key
    overrides: dict = {"investigation": {"max_tool_calls": inputs.max_tool_calls}}
    live = inputs.as_of is None and not inputs.before_first_patch
    if inputs.use_source_checkout and live and ctx.config.source is not None:
        overrides["sources"] = {"local_repo": str(await ctx.prepare_repo())}

    log_file = ctx.log_path.open("a", encoding="utf-8")

    def progress(message: str) -> None:
        print(message, file=sys.stderr, flush=True)
        log_file.write(message + "\n")
        log_file.flush()

    try:
        outcome = await run_crash_info(
            inputs.bug_id,
            load_settings(overrides=overrides),
            as_of=inputs.as_of,
            before_first_patch=inputs.before_first_patch,
            model=inputs.model,
            effort=inputs.effort,
            run_id=ctx.run_id,
            progress=progress,
            log_path=ctx.log_path,
            verbose=True,
        )
    except ValueError as e:  # e.g. a bug with no patch to cut off before
        raise AgentError(str(e)) from e
    finally:
        log_file.close()

    ctx.publish_json(STATE_ARTIFACT, outcome.state.model_dump(mode="json"))
    ctx.publish_json(REPORT_ARTIFACT, outcome.report)
    markdown = ctx.log_path.parent / "report.md"
    markdown.write_text(outcome.markdown, encoding="utf-8")
    ctx.publish_file(MARKDOWN_ARTIFACT, markdown, "text/markdown; charset=utf-8")
    handoff = ctx.log_path.parent / "handoff.md"
    handoff.write_text(render_handoff(outcome.state, full=True), encoding="utf-8")
    ctx.publish_file(HANDOFF_ARTIFACT, handoff, "text/markdown; charset=utf-8")
    return outcome.result


if __name__ == "__main__":
    run_async(main)
