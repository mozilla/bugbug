"""Run the bug-fix agent locally, without Docker or the broker sidecar.

Builds the read-only Bugzilla MCP server in-process, so this script sees the
Bugzilla API key directly — unlike the deployed agent, which reaches a broker
sidecar over HTTP and never holds the key. Handy for quick iteration; for a
faithful end-to-end run use ``docker compose -f compose.yml up``.
"""

import asyncio
import sys
from pathlib import Path

import bugsy
from pydantic_settings import BaseSettings, SettingsConfigDict

# Make the co-located `hackbot_agents` namespace importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_tools import bugzilla  # noqa: E402
from agent_tools.bugzilla import BugzillaContext  # noqa: E402
from agent_tools.claude_sdk import build_sdk_server  # noqa: E402
from hackbot_agents.bug_fix import run_bug_fix  # noqa: E402


class Settings(BaseSettings):
    bug_id: int
    bugzilla_api_url: str = "https://bugzilla.mozilla.org/rest"
    bugzilla_api_key: str
    source_repo: Path
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(
        cli_parse_args=True,
        env_file=".env",
        extra="ignore",
    )


async def main():
    settings = Settings()

    bugzilla_mcp_server = build_sdk_server(
        "bugzilla",
        BugzillaContext(
            client=bugsy.Bugsy(
                api_key=settings.bugzilla_api_key,
                bugzilla_url=settings.bugzilla_api_url,
            ),
        ),
        bugzilla.TOOLS,
    )

    result = await run_bug_fix(
        bugzilla_mcp_server=bugzilla_mcp_server,
        source_repo=settings.source_repo,
        model=settings.model,
        max_turns=settings.max_turns,
        effort=settings.effort,
        bugs=[settings.bug_id],
        verbose=True,
    )
    print(f"\nexit_code={result.exit_code} bugs_processed={result.bugs_processed}")


if __name__ == "__main__":
    asyncio.run(main())
