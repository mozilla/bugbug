from hackbot_runtime import HackbotContext, run_async
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import AutowebcompatReproResult, run_autowebcompat_repro
from .firefox_install import install_firefox_nightly


class AgentInputs(BaseSettings):
    bugzilla_mcp_url: str
    bug_data: str | None = None
    bug_id: int | None = None
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(extra="ignore")


async def main(ctx: HackbotContext) -> AutowebcompatReproResult:
    inputs = AgentInputs()

    # Provision a fresh Nightly at startup so each run reproduces against a
    # current build; drive the binary the install reports back.
    firefox_path = str(install_firefox_nightly())

    return await run_autowebcompat_repro(
        bugzilla_mcp_server={
            "type": "http",
            "url": inputs.bugzilla_mcp_url,
        },
        bug_data=inputs.bug_data,
        bug_id=inputs.bug_id,
        model=inputs.model,
        max_turns=inputs.max_turns,
        effort=inputs.effort,
        firefox_path=firefox_path,
        log=ctx.log_path,
        verbose=True,
    )


if __name__ == "__main__":
    run_async(main)
