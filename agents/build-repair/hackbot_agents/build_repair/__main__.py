import os

from hackbot_runtime import BaseAgentInputs, HackbotContext, run_async

from .agent import BuildRepairResult, run_build_repair


class AgentInputs(BaseAgentInputs):
    bug_id: int | None = None
    git_commit: str
    failure_tasks: dict[str, str]
    bugzilla_mcp_url: str
    run_try_push: bool = False
    model: str | None = None
    max_turns: int | None = None


async def main(ctx: HackbotContext) -> BuildRepairResult:
    inputs = ctx.load_inputs(AgentInputs)

    # The build failure lives at this commit; pin the checkout there before the
    # runtime prepares the source tree (consumed in HackbotContext.source_repo).
    os.environ.setdefault("SOURCE_REF", inputs.git_commit)

    return await run_build_repair(
        bugzilla_mcp_server={
            "type": "http",
            "url": inputs.bugzilla_mcp_url,
        },
        source_repo=ctx.source_repo,
        fx_ctx=ctx.firefox,
        bug_id=inputs.bug_id,
        git_commit=inputs.git_commit,
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
