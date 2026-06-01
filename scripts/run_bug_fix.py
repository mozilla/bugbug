"""Run the bug_fix tool locally."""

import asyncio
from pathlib import Path

import bugsy
from pydantic_settings import BaseSettings, SettingsConfigDict

from bugbug.tools.bug_fix.agent import BugFixTool
from bugbug.tools.bug_fix.bugzilla_mcp import BugzillaContext, build_server


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

    bugzilla_mcp_server = build_server(
        BugzillaContext(
            client=bugsy.Bugsy(
                api_key=settings.bugzilla_api_key,
                bugzilla_url=settings.bugzilla_api_url,
            ),
            dry_run=True,
        )
    )

    tool = BugFixTool.create()
    result = await tool.run(
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
