import os

from hackbot_runtime import HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import BuildRepairResult, run_build_repair
from .resolve import resolve_git_commits


class AgentInputs(BaseSettings):
    failure_tasks: dict[str, str]
    git_commit: str | None = None
    bug_id: int | None = None
    bugzilla_mcp_url: str
    run_try_push: bool = False
    model: str | None = None
    max_turns: int | None = None

    # Compose passes unset per-run inputs as empty strings (``${BUG_ID:-}``);
    # treat those as absent so optional fields fall back to their defaults.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)


async def main(ctx: HackbotContext) -> BuildRepairResult:
    inputs = AgentInputs()

    if not inputs.failure_tasks:
        raise ValueError("failure_tasks must contain at least one task")
    # Resolve the push commits from any of the failing tasks (all share a push).
    # The first is the failure commit the tree is checked out at; the rest let
    # the agent blame the culprit.
    task_id = next(iter(inputs.failure_tasks.values()))
    git_commits = resolve_git_commits(task_id, inputs.git_commit)

    # Pin the checkout to the failure commit and fetch deep enough to include the
    # whole push, so the agent can `git show` every commit in it. Both are read
    # when the runtime prepares the source tree (HackbotContext.source_repo).
    os.environ.setdefault("SOURCE_REF", git_commits[0])
    os.environ.setdefault("SOURCE_DEPTH", str(len(git_commits) + 1))

    return await run_build_repair(
        bugzilla_mcp_server={
            "type": "http",
            "url": inputs.bugzilla_mcp_url,
        },
        source_repo=ctx.source_repo,
        fx_ctx=ctx.firefox,
        bug_id=inputs.bug_id,
        git_commits=git_commits,
        failure_tasks=inputs.failure_tasks,
        run_try_push=inputs.run_try_push,
        model=inputs.model,
        max_turns=inputs.max_turns,
        log=ctx.log_path,
        verbose=True,
        publish_file=ctx.publish_file,
    )


if __name__ == "__main__":
    run_async(main)
