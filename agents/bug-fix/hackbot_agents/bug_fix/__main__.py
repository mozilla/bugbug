from hackbot_runtime import HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import BugFixResult, run_bug_fix


class AgentInputs(BaseSettings):
    bug_id: int
    bugzilla_mcp_url: str
    # Follow-up mode: update an existing Phabricator revision, acting on a
    # reviewer's comment supplied as free-text instructions.
    revision_id: int | None = None
    instructions: str | None = None
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(extra="ignore")


async def main(ctx: HackbotContext) -> BugFixResult:
    inputs = AgentInputs()

    await ctx.prepare_repo()

    # A plain run triages and fixes the bug; a follow-up run lets run_bug_fix
    # build a revision-specific task from the reviewer's comment instead.
    task = None if inputs.revision_id else "Triage and fix the bug, and verify the fix"

    return await run_bug_fix(
        task=task,
        bugzilla_mcp_server={
            "type": "http",
            "url": inputs.bugzilla_mcp_url,
        },
        source_repo=ctx.repo_path,
        fx_ctx=ctx.firefox,
        bug=inputs.bug_id,
        revision_id=inputs.revision_id,
        instructions=inputs.instructions or "",
        model=inputs.model,
        max_turns=inputs.max_turns,
        effort=inputs.effort,
        log=ctx.log_path,
        verbose=True,
        actions_recorder=ctx.actions,
    )


if __name__ == "__main__":
    run_async(main)
