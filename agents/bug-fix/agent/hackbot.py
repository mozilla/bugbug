import tempfile
from pathlib import Path

from hackbot_runtime import AgentResult, Context, ensure_source_repo
from pydantic_settings import BaseSettings, SettingsConfigDict

FIREFOX_REPO_URL = "https://github.com/mozilla-firefox/firefox.git"


class AgentInputs(BaseSettings):
    bug_id: int
    bugzilla_mcp_url: str
    source_repo: Path = Path("/workspace/firefox")
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(extra="ignore")


async def main(ctx: Context) -> AgentResult:
    from . import run_bug_fix

    inputs = AgentInputs()
    ensure_source_repo(inputs.source_repo, FIREFOX_REPO_URL)

    log_path = Path(tempfile.mkdtemp(prefix="bug-fix-log-")) / "agent.log"

    result = await run_bug_fix(
        task="Triage and fix the bug, and verify the fix",
        bugzilla_mcp_server={
            "type": "http",
            "url": inputs.bugzilla_mcp_url,
        },
        source_repo=inputs.source_repo,
        bugs=[inputs.bug_id],
        model=inputs.model,
        max_turns=inputs.max_turns,
        effort=inputs.effort,
        log=log_path,
        verbose=True,
        actions_recorder=ctx.actions,
    )

    if log_path.exists():
        # Uploaded when a signed policy is set, else copied into ./artifacts.
        ctx.publish_file("logs/agent.log", log_path, "text/plain")

    return AgentResult(
        status="ok" if result.exit_code == 0 else "error",
        error=None if result.exit_code == 0 else f"exit_code={result.exit_code}",
        findings={
            "exit_code": result.exit_code,
            "bugs_processed": result.bugs_processed,
        },
        exit_code=result.exit_code,
    )
