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

    Idempotent and recovers from a partial checkout left by an earlier
    failed run (e.g. clone succeeded but checkout ran out of disk).
    """
    if (source_repo / ".git").exists():
        status = subprocess.run(
            ["git", "-C", str(source_repo), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
        # A healthy fresh shallow clone has an empty status; a broken
        # checkout shows thousands of missing-file "D" entries.
        if status.stdout.strip():
            log.warning(
                "firefox source at %s is incomplete; restoring working tree",
                source_repo,
            )
            subprocess.run(
                ["git", "-C", str(source_repo), "restore", "--source=HEAD", ":/"],
                check=True,
                stdout=sys.stderr,
                stderr=sys.stderr,
            )
        log.info("updating firefox source at %s (shallow fetch)", source_repo)
        subprocess.run(
            ["git", "-C", str(source_repo), "fetch", "--depth=1", "origin", "HEAD"],
            check=True,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )
        subprocess.run(
            ["git", "-C", str(source_repo), "reset", "--hard", "FETCH_HEAD"],
            check=True,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )
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


if __name__ == "__main__":
    raise SystemExit(run_async(main))
