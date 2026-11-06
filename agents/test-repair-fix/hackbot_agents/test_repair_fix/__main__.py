import json
import logging
import shutil
import tempfile
from pathlib import Path

from hackbot_agents.test_repair.agent import TestRepairResult
from hackbot_agents.test_repair.notify import resolve_culprit_author
from hackbot_agents.test_repair.resolve import (
    INVESTIGATION_ARTIFACT,
    Investigation,
    sheriff_classification,
)
from hackbot_runtime import AgentError, HackbotContext, run_async
from hackbot_runtime.actions.email import record_email
from hackbot_runtime.actions.phabricator import PATCH_ACTION_TYPES
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import TestRepairFixResult
from .config import SKIP_FIREFOX_BUILD
from .notify import build_email, recipients

logger = logging.getLogger(__name__)


class AgentInputs(BaseSettings):
    # When set, the agent proposes a patch but cannot build or run it.
    skip_firefox_build: bool = SKIP_FIREFOX_BUILD
    bugzilla_mcp_url: str = ""
    model: str | None = None
    max_turns: int | None = None

    # Compose passes unset per-run inputs as empty strings; treat those as absent.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)


def _load_analysis(parent: Path) -> tuple[TestRepairResult, str, Investigation]:
    """The verdict, task id and investigation of the test-repair run."""
    summary = json.loads((parent / "summary.json").read_text())
    analysis = TestRepairResult.model_validate(summary["findings"])
    if not analysis.culprit_commit:
        raise AgentError("the test-repair run blamed no commit, so nothing to fix")
    handoff = json.loads((parent / INVESTIGATION_ARTIFACT).read_text())
    return (
        analysis,
        handoff["task_id"],
        Investigation.from_dict(handoff["investigation"]),
    )


async def main(ctx: HackbotContext) -> TestRepairFixResult:
    from .agent import run_fix

    inputs = AgentInputs()
    parent = ctx.parent_artifacts_dir
    analysis, task_id, investigation = _load_analysis(parent)
    logger.info("Fixing %s for task %s", analysis.culprit_commit, task_id)

    scratch_dir = Path(tempfile.mkdtemp(prefix="test-repair-fix-"))
    analysis_dir = scratch_dir / "analysis"
    shutil.copytree(
        parent, analysis_dir, ignore=shutil.ignore_patterns("summary.json", "agent.log")
    )
    out_dir = scratch_dir / "out"
    out_dir.mkdir()

    source_repo = await ctx.prepare_repo(ref=analysis.culprit_commit)
    result = await run_fix(
        bugzilla_mcp_server=(
            {"type": "http", "url": inputs.bugzilla_mcp_url}
            if inputs.bugzilla_mcp_url
            else None
        ),
        source_repo=source_repo,
        fx_ctx=ctx.firefox,
        investigation=investigation,
        analysis=analysis,
        analysis_dir=analysis_dir,
        out_dir=out_dir,
        skip_firefox_build=inputs.skip_firefox_build,
        model=inputs.model,
        max_turns=inputs.max_turns,
        log=ctx.log_path,
        verbose=True,
        publish_file=ctx.publish_file,
        actions_recorder=ctx.actions,
    )

    if result.proposed_patch:
        try:
            _record_patch_email(ctx, result, investigation, task_id, source_repo)
        except Exception:
            # A notification is never worth losing a finished patch over.
            logger.exception("Could not record the patch email")
    return result


def _record_patch_email(
    ctx: HackbotContext,
    result: TestRepairFixResult,
    investigation: Investigation,
    task_id: str,
    source_repo: Path,
) -> None:
    """Email the patch to the culprit's author, and to the team apply-side."""
    pending = next(
        (a for a in ctx.actions.actions if a["type"] in PATCH_ACTION_TYPES), None
    )
    culprit_author = resolve_culprit_author(source_repo, result.culprit_commit)
    classification = sheriff_classification(investigation.project, task_id)
    subject, body = build_email(
        result,
        investigation,
        task_id=task_id,
        run_id=ctx.run_id,
        analysis_run_id=ctx.parent_run_id,
        culprit_author=culprit_author,
        classification=classification,
        revision_pending=pending is not None,
        parent_revision=(pending or {}).get("params", {}).get("parent_revision_id"),
    )
    record_email(
        ctx.actions,
        to=recipients(result, culprit_author, classification),
        subject=subject,
        body_markdown=body,
        attach_patch=ctx.source_changed,
    )


if __name__ == "__main__":
    run_async(main)
