import asyncio
import sys
from pathlib import Path

from hackbot_runtime import HackbotContext, run_async
from hackbot_runtime.searchfox import resolve_index_revision
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import (
    DEFAULT_MAX_BUDGET_USD,
    DEFAULT_MAX_TURNS,
    GeckoTriageResult,
    run_gecko_triage,
)
from .dupes import find_candidates
from .preflight import require_state, skip_reason
from .state import fetch_state
from .toolkit import DEFAULT_TOOLKIT_DIR, require_toolkit

TRIAGE_TASK = (
    "Triage this bug following the rules: inventory what the bug already "
    "contains, read every diagnostic artifact you can, decide between asking "
    "for information, triaging, or proposing a close or move, and record one comment "
    "plus the field changes the rules allow. Do not build, run or modify the source."
)

# Opus 5.5's context window is natively 1M, so the bare id needs no `[1m]` suffix.
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"


class AgentInputs(BaseSettings):
    bug_id: int
    broker_url: str
    model: str = DEFAULT_MODEL
    max_turns: int = DEFAULT_MAX_TURNS
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD
    effort: str = DEFAULT_EFFORT
    fx_bug_toolkit_dir: Path = Path(DEFAULT_TOOLKIT_DIR)

    model_config = SettingsConfigDict(extra="ignore")

    @property
    def bugzilla_mcp_url(self) -> str:
        return f"{self.broker_url.rstrip('/')}/mcp"


async def main(ctx: HackbotContext) -> GeckoTriageResult:
    inputs = AgentInputs()
    require_toolkit(inputs.fx_bug_toolkit_dir)
    bugzilla_mcp_server = {"type": "http", "url": inputs.bugzilla_mcp_url}

    # Ahead of `prepare_repo` and the model: a skipped bug costs neither.
    state = require_state(await fetch_state(bugzilla_mcp_server, inputs.bug_id))
    reason = skip_reason(state)
    if reason:
        print(f"[gecko_triage] skipping bug {inputs.bug_id}: {reason}", file=sys.stderr)
        # Returned, not raised: this run did what it should have.
        return GeckoTriageResult(
            bug_id=inputs.bug_id,
            num_turns=0,
            total_cost_usd=0.0,
            product=state.bug.get("product"),
            component=state.bug.get("component"),
            decision="skipped",
            result=f"Skipped bug {inputs.bug_id}: {reason}. No triage was run.",
        )

    # `prepare_repo` blocks on the clone, so it runs in a thread beside the searches.
    _, searchfox_rev, duplicate_candidates = await asyncio.gather(
        asyncio.to_thread(asyncio.run, ctx.prepare_repo()),
        resolve_index_revision(),
        find_candidates(bugzilla_mcp_server, state.bug),
    )
    if not searchfox_rev:
        print(
            "[gecko_triage] no searchfox revision; linking tip-of-tree", file=sys.stderr
        )

    return await run_gecko_triage(
        task=TRIAGE_TASK,
        bugzilla_mcp_server=bugzilla_mcp_server,
        source_repo=ctx.repo_path,
        toolkit_dir=inputs.fx_bug_toolkit_dir,
        bug=inputs.bug_id,
        state=state,
        duplicate_candidates=duplicate_candidates,
        searchfox_rev=searchfox_rev,
        model=inputs.model,
        max_turns=inputs.max_turns,
        max_budget_usd=inputs.max_budget_usd,
        effort=inputs.effort,
        log=ctx.log_path,
        verbose=True,
        actions_recorder=ctx.actions,
    )


if __name__ == "__main__":
    run_async(main)
