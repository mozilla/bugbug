import logging
import tempfile
from pathlib import Path

from hackbot_runtime import HackbotContext, run_async
from hackbot_runtime.actions.email import record_email
from hackbot_runtime.actions.slack import record_message
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import TestRepairResult
from .config import SKIP_FIREFOX_BUILD, SLACK_CHANNEL
from .notify import (
    build_email,
    build_message,
    resolve_culprit_author,
    sheriff_action_required,
)
from .resolve import Investigation, resolve_investigation, sheriff_classification

logger = logging.getLogger(__name__)


class AgentInputs(BaseSettings):
    # {task_name: task_id}. Everything about the failure is resolved from the task id.
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
    scratch_out = scratch_dir / "out"
    scratch_out.mkdir(parents=True, exist_ok=True)

    bugzilla_mcp_server = (
        {"type": "http", "url": inputs.bugzilla_mcp_url}
        if inputs.bugzilla_mcp_url
        else None
    )
    ref, depth = _checkout_pin(investigation)
    logger.info("Pinning checkout to %s with depth %s", ref, depth)
    source_repo = await ctx.prepare_repo(ref=ref, depth=depth)

    result = await run_test_repair(
        bugzilla_mcp_server=bugzilla_mcp_server,
        source_repo=source_repo,
        fx_ctx=ctx.firefox,
        investigation=investigation,
        scratch_out=scratch_out,
        skip_firefox_build=inputs.skip_firefox_build,
        model=inputs.model,
        max_turns=inputs.max_turns,
        log=ctx.log_path,
        verbose=True,
        publish_file=ctx.publish_file,
    )

    culprit_author = resolve_culprit_author(source_repo, result.culprit_commit)
    if sheriff_action_required(result):
        message = build_message(
            result,
            investigation,
            task_id=task_id,
            run_id=ctx.run_id,
            culprit_author=culprit_author,
        )
        record_message(ctx.actions, SLACK_CHANNEL, message)
    else:
        logger.info(
            "Verdict is %s; not notifying %s", result.classification, SLACK_CHANNEL
        )

    try:
        _record_verdict_email(ctx, result, investigation, task_id, culprit_author)
    except Exception:
        # A notification is never worth losing a finished analysis over.
        logger.exception("Could not record the verdict email")
    return result


def _record_verdict_email(
    ctx: HackbotContext,
    result: TestRepairResult,
    investigation: Investigation,
    task_id: str,
    culprit_author: str | None,
) -> None:
    """Email every verdict to the team, actionable or not.

    Unlike the Slack message this is not filtered: the team tracks what the agent
    decided, including the intermittents no sheriff has to act on.
    """
    patch = ctx.source_patch
    subject, body = build_email(
        result,
        investigation,
        task_id=task_id,
        run_id=ctx.run_id,
        patch=patch,
        culprit_author=culprit_author,
        already_actioned=sheriff_classification(investigation.project, task_id),
    )
    record_email(
        ctx.actions,
        subject=subject,
        body_markdown=body,
        attach_artifacts=["changes/changes.patch"] if patch else [],
    )


if __name__ == "__main__":
    run_async(main)
