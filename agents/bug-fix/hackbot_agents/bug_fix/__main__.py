from hackbot_runtime import HackbotContext, checkout_revision, run_async
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import BugFixResult, run_bug_fix


class AgentInputs(BaseSettings):
    bug_id: int
    bugzilla_mcp_url: str
    revision_id: int | None = None
    comment: str | None = None
    phabricator_broker_url: str | None = None
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(extra="ignore")

    @model_validator(mode="after")
    def _broker_url_required_for_follow_up(self) -> "AgentInputs":
        # A follow-up (revision_id set) must be able to fetch the revision's
        # patch from the broker to check it out.
        if self.revision_id is not None and not self.phabricator_broker_url:
            raise ValueError(
                "phabricator_broker_url (PHABRICATOR_BROKER_URL) is required when "
                "revision_id is set, to check out the revision"
            )
        return self


async def main(ctx: HackbotContext) -> BugFixResult:
    inputs = AgentInputs()

    if inputs.revision_id:
        await checkout_revision(ctx, inputs.revision_id, inputs.phabricator_broker_url)
    else:
        await ctx.prepare_repo()

    return await run_bug_fix(
        bugzilla_mcp_server={
            "type": "http",
            "url": inputs.bugzilla_mcp_url,
        },
        source_repo=ctx.repo_path,
        fx_ctx=ctx.firefox,
        bug=inputs.bug_id,
        revision_id=inputs.revision_id,
        comment=inputs.comment,
        model=inputs.model,
        max_turns=inputs.max_turns,
        effort=inputs.effort,
        log=ctx.log_path,
        verbose=True,
        actions_recorder=ctx.actions,
    )


if __name__ == "__main__":
    run_async(main)
