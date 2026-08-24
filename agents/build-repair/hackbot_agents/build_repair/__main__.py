import logging

from hackbot_runtime import HackbotContext, changes, run_async
from hackbot_runtime.actions.email import record_email
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import BuildRepairResult, run_build_repair
from .config import NOTIFY_ONLY_WITH_PATCH
from .notify import build_email, recipients, resolve_author_email
from .resolve import PushInfo, resolve_push

logger = logging.getLogger(__name__)


class AgentInputs(BaseSettings):
    failure_tasks: dict[str, str]
    git_commit: str | None = None
    bug_id: int | None = None
    broker_url: str
    run_try_push: bool = False
    model: str | None = None
    max_turns: int | None = None

    # Compose passes unset per-run inputs as empty strings (``${BUG_ID:-}``);
    # treat those as absent so optional fields fall back to their defaults.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)

    @property
    def bugzilla_mcp_url(self) -> str:
        return f"{self.broker_url.rstrip('/')}/mcp"


async def main(ctx: HackbotContext) -> BuildRepairResult:
    inputs = AgentInputs()

    if not inputs.failure_tasks:
        raise ValueError("failure_tasks must contain at least one task")
    # Resolve the push commits from any of the failing tasks (all share a push).
    # The first is the failure commit the tree is checked out at; the rest let
    # the agent blame the culprit.
    task_id = next(iter(inputs.failure_tasks.values()))
    push = resolve_push(task_id, inputs.git_commit)
    git_commits = push.git_commits

    # Pin the checkout to the failure commit and fetch deep enough to include the
    # whole push, so the agent can `git show` every commit in it.
    await ctx.prepare_repo(ref=push.git_commits[0], depth=len(push.git_commits) + 1)

    result = await run_build_repair(
        bugzilla_mcp_server={
            "type": "http",
            "url": inputs.bugzilla_mcp_url,
        },
        source_repo=ctx.repo_path,
        fx_ctx=ctx.firefox,
        bug_id=inputs.bug_id,
        git_commits=git_commits,
        project=push.project,
        hg_revision=push.hg_revision,
        failure_tasks=inputs.failure_tasks,
        run_try_push=inputs.run_try_push,
        model=inputs.model,
        max_turns=inputs.max_turns,
        log=ctx.log_path,
        verbose=True,
        publish_file=ctx.publish_file,
    )

    try:
        _record_analysis_email(ctx, result, push, task_id)
    except Exception:
        # A notification is never worth losing a finished analysis over.
        logger.exception("Could not record the failure-analysis email")
    return result


def _record_analysis_email(
    ctx: HackbotContext, result: BuildRepairResult, push: PushInfo, task_id: str
) -> None:
    patch = (
        changes.pending_patch(ctx.repo_path, ctx.source_base) if ctx.source_base else ""
    )
    if NOTIFY_ONLY_WITH_PATCH and not patch:
        logger.info("Run produced no patch; not emailing the failure analysis")
        return

    blamed_author = resolve_author_email(ctx.repo_path, result.blamed_commit)
    subject, body = build_email(
        result,
        push,
        task_id=task_id,
        run_id=ctx.run_id,
        patch=patch,
        blamed_author=blamed_author,
    )
    record_email(
        ctx.actions,
        to=recipients(push, blamed_author),
        subject=subject,
        body_markdown=body,
        attach_artifacts=["changes/changes.patch"] if patch else [],
    )


if __name__ == "__main__":
    run_async(main)
