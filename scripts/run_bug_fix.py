"""Run the bug_fix tool locally."""

import asyncio
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from bugbug.tools.bug_fix.agent import BugFixTool


class Settings(BaseSettings):
    bug_id: int
    bugzilla_api_url: str | None = "https://bugzilla.mozilla.org/rest"
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
    tool = BugFixTool.create()
    result = await tool.run(
        base_url=settings.bugzilla_api_url,
        api_key=settings.bugzilla_api_key,
        source_repo=settings.source_repo,
        model=settings.model,
        max_turns=settings.max_turns,
        effort=settings.effort,
        bugs=[settings.bug_id],
        dry_run=True,
        verbose=True,
    )
    print(f"\nexit_code={result.exit_code} bugs_processed={result.bugs_processed}")
    if result.simulated_writes:
        print(f"simulated writes: {len(result.simulated_writes)}")


if __name__ == "__main__":
    asyncio.run(main())
