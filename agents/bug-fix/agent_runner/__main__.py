import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from hackbot_runtime import AgentResult, Context, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("bug-fix-agent")

FIREFOX_REPO_URL = "https://github.com/mozilla-firefox/firefox.git"


class AgentInputs(BaseSettings):
    bug_id: int
    bugzilla_mcp_url: str
    source_repo: Path = Path("/workspace/firefox")
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(extra="ignore")


def ensure_firefox_source(source_repo: Path) -> None:
    """Shallow-clone the Firefox source tree if it isn't already present.

    Idempotent: a mounted volume or pre-baked image with an existing
    checkout short-circuits the clone.
    """
    if (source_repo / ".git").exists():
        log.info("firefox source already present at %s", source_repo)
        return
    source_repo.mkdir(parents=True, exist_ok=True)
    log.info("cloning firefox source (shallow) to %s", source_repo)
    subprocess.run(
        ["git", "clone", "--depth=1", FIREFOX_REPO_URL, str(source_repo)],
        check=True,
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
    log.info("firefox shallow clone complete")


async def main(ctx: Context) -> AgentResult:
    from bugbug.tools.bug_fix.agent import BugFixTool

    inputs = AgentInputs()
    ensure_firefox_source(inputs.source_repo)

    log_path = Path(tempfile.mkdtemp(prefix="bug-fix-log-")) / "agent.log"

    tool = BugFixTool.create()
    result = await tool.run(
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
    )

    if log_path.exists() and ctx.uploader is not None:
        ctx.uploader.upload_file("logs/agent.log", log_path, "text/plain")

    return AgentResult(
        status="ok" if result.exit_code == 0 else "error",
        error=None if result.exit_code == 0 else f"exit_code={result.exit_code}",
        findings={
            "exit_code": result.exit_code,
            "bugs_processed": result.bugs_processed,
        },
        exit_code=result.exit_code,
    )


if __name__ == "__main__":
    raise SystemExit(run_async(main))
