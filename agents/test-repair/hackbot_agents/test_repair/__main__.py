import logging
import os
import tempfile
from pathlib import Path

from hackbot_runtime import HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import TestRepairResult
from .logs import download_failure_logs
from .resolve import Investigation, resolve_investigation

logger = logging.getLogger(__name__)


class AgentInputs(BaseSettings):
    # Failing Taskcluster test tasks {task_name: task_id}. The agent resolves the
    # push, last-green revision and candidate commit range from the task id.
    failure_tasks: dict[str, str]
    bugzilla_mcp_url: str = ""
    model: str | None = None
    max_turns: int | None = None

    # Compose passes unset per-run inputs as empty strings; treat those as absent.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)


def _pin_checkout(candidate_commits: list[str]) -> None:
    """Pin the shallow clone to the failure commit, deep enough for the range.

    ``SOURCE_REF`` is the head (failure) commit and ``SOURCE_DEPTH`` spans back to
    the last-green commit so the agent can ``git show`` every candidate. Read by
    the runtime when it prepares the source tree (HackbotContext.source_repo).
    """
    os.environ.setdefault("SOURCE_REF", candidate_commits[0])
    os.environ.setdefault("SOURCE_DEPTH", str(len(candidate_commits) + 1))
    logger.info(
        "Pinning checkout to %s with depth %s",
        os.environ["SOURCE_REF"],
        os.environ["SOURCE_DEPTH"],
    )


async def main(ctx: HackbotContext) -> TestRepairResult:
    from .agent import run_test_repair

    inputs = AgentInputs()
    if not inputs.failure_tasks:
        raise ValueError("failure_tasks must contain at least one task")

    task_id = next(iter(inputs.failure_tasks.values()))
    logger.info("Starting test-repair for task %s", task_id)
    investigation: Investigation = resolve_investigation(task_id)
    _pin_checkout(investigation.candidate_commits)

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
    return await run_test_repair(
        bugzilla_mcp_server=bugzilla_mcp_server,
        source_repo=ctx.source_repo,
        fx_ctx=ctx.firefox,
        investigation=investigation,
        task_logs=task_logs,
        scratch_out=scratch_out,
        model=inputs.model,
        max_turns=inputs.max_turns,
        log=ctx.log_path,
        verbose=True,
        publish_file=ctx.publish_file,
    )


if __name__ == "__main__":
    run_async(main)
