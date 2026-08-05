import logging
import tempfile
from pathlib import Path

from hackbot_runtime import HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import TestRepairResult
from .config import SKIP_FIREFOX_BUILD
from .logs import download_failure_logs
from .resolve import Investigation, resolve_investigation

logger = logging.getLogger(__name__)


class AgentInputs(BaseSettings):
    # Failing Taskcluster test tasks {task_name: task_id}. The agent resolves the
    # push, last-green revision and candidate commit range from the task id.
    failure_tasks: dict[str, str]
    # When set, the fix stage proposes a patch but cannot build or run it.
    skip_firefox_build: bool = SKIP_FIREFOX_BUILD
    bugzilla_mcp_url: str = ""
    model: str | None = None
    max_turns: int | None = None

    # Compose passes unset per-run inputs as empty strings; treat those as absent.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)


def _checkout_pin(investigation: Investigation) -> tuple[str, int]:
    """The failure commit and a fetch depth deep enough to reach the range base."""
    return investigation.failure_commit, investigation.commit_range.span + 1


async def main(ctx: HackbotContext) -> TestRepairResult:
    from .agent import run_test_repair

    inputs = AgentInputs()
    if not inputs.failure_tasks:
        raise ValueError("failure_tasks must contain at least one task")

    task_id = next(iter(inputs.failure_tasks.values()))
    logger.info("Starting test-repair for task %s", task_id)
    investigation: Investigation = resolve_investigation(task_id)

    scratch_dir = Path(tempfile.mkdtemp(prefix="test-repair-"))
    scratch_in = scratch_dir / "in"
    scratch_out = scratch_dir / "out"
    scratch_in.mkdir(parents=True, exist_ok=True)
    scratch_out.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading failure logs for %d task(s)", len(inputs.failure_tasks))
    task_logs = await download_failure_logs(inputs.failure_tasks, scratch_in)

    bugzilla_mcp_server = (
        {"type": "http", "url": inputs.bugzilla_mcp_url}
        if inputs.bugzilla_mcp_url
        else None
    )
    ref, depth = _checkout_pin(investigation)
    logger.info("Pinning checkout to %s with depth %s", ref, depth)
    source_repo = await ctx.prepare_repo(ref=ref, depth=depth)

    return await run_test_repair(
        bugzilla_mcp_server=bugzilla_mcp_server,
        source_repo=source_repo,
        fx_ctx=ctx.firefox,
        investigation=investigation,
        task_logs=task_logs,
        scratch_out=scratch_out,
        skip_firefox_build=inputs.skip_firefox_build,
        model=inputs.model,
        max_turns=inputs.max_turns,
        log=ctx.log_path,
        verbose=True,
        publish_file=ctx.publish_file,
    )


if __name__ == "__main__":
    run_async(main)
