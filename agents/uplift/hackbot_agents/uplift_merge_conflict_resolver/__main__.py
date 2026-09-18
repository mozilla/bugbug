import logging

from hackbot_runtime import HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import UpliftResult, run_uplift
from .config import UpliftSource

logger = logging.getLogger(__name__)


class AgentInputs(BaseSettings):
    """The per-run inputs, read from the environment the platform sets.

    Mirrors `UpliftInputs` in hackbot-api, which upper-cases each field name
    and JSON-encodes lists. Deploy-time constants arrive the same way, from the
    Job's static env rather than the caller.
    """

    # The stable branch ref to uplift onto, e.g. `release`, `beta`, `esr128`.
    target_branch: str

    # The exact commit to uplift onto. A branch name moves, so a caller
    # reproducing a specific uplift should pin it; otherwise the tip is used.
    target_commit: str | None = None

    # The patches to apply onto the branch, in this order; a stack is several.
    sources: list[UpliftSource]

    # The bugbug MCP, serving the bug, revision and fx-docs tools.
    bugbug_mcp_url: str

    # The broker sidecar, whose Conduit proxy diffs are fetched through.
    broker_url: str

    # The originating bug, for context when a conflict's intent is unclear.
    bug_id: int | None = None

    # Overrides; `None` leaves the SDK's or the API's own default in place.
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    # Compose passes unset inputs as empty strings (``${BUG_ID:-}``).
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)


async def main(ctx: HackbotContext) -> UpliftResult:
    inputs = AgentInputs()

    # Pin the checkout before anything else reads the tree.
    ref = inputs.target_commit or inputs.target_branch
    logger.debug("Preparing the source checkout at %s.", ref)
    source_repo = await ctx.prepare_repo(ref=ref)

    return await run_uplift(
        bugbug_mcp_server={
            "type": "http",
            "url": inputs.bugbug_mcp_url,
        },
        broker_url=inputs.broker_url,
        source_repo=source_repo,
        target_branch=inputs.target_branch,
        target_commit=inputs.target_commit,
        sources=inputs.sources,
        bug_id=inputs.bug_id,
        model=inputs.model,
        max_turns=inputs.max_turns,
        effort=inputs.effort,
        log=ctx.log_path,
        verbose=True,
        publish_file=ctx.publish_file,
    )


if __name__ == "__main__":
    run_async(main)
