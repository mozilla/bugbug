from typing import Annotated
from urllib.parse import urlsplit

from hackbot_runtime import (
    HackbotContext,
    checkout_pull_request,
    checkout_revision,
    run_async,
)
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import BugFixResult, PullRequest, run_bug_fix

# What to fetch as a pull request's base when the caller doesn't name it: the
# source repository's default branch.
DEFAULT_BASE_REF = "HEAD"


class AgentInputs(BaseSettings):
    # Required unless following up on a GitHub pull request.
    bug_id: int | None = None
    broker_url: str
    revision_id: int | None = None
    comment: str | None = None
    github_repo: Annotated[
        str | None, Field(pattern=r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
    ] = None
    github_pr_number: Annotated[int | None, Field(gt=0)] = None
    github_head_sha: Annotated[str | None, Field(pattern=r"^[0-9a-f]{40}$")] = None
    # Passed to ``git fetch``, so it must not read as an option or a refspec.
    github_base_ref: Annotated[str | None, Field(pattern=r"^[^-\s:][^\s:]*$")] = None
    bugzilla_needinfo_flag_id: int | None = None
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    model_config = SettingsConfigDict(extra="ignore")

    def broker_endpoint(self, path: str) -> str:
        return f"{self.broker_url.rstrip('/')}/{path.lstrip('/')}"

    @property
    def bugzilla_mcp_url(self) -> str:
        return self.broker_endpoint("/bugzilla/mcp")

    @property
    def phabricator_mcp_url(self) -> str:
        """The broker's Phabricator MCP endpoint.

        Derived from the broker URL rather than taken as its own input: both
        endpoints are served by the same sidecar, so there is nothing for a
        caller to configure independently.
        """
        return self.broker_endpoint("/phabricator/mcp")

    @model_validator(mode="after")
    def _validate_mode(self) -> AgentInputs:
        """Require one coherent normal, Phabricator, GitHub, or Bugzilla mode."""
        if (self.github_repo is None) != (self.github_pr_number is None):
            raise ValueError(
                "github_repo (GITHUB_REPO) and github_pr_number "
                "(GITHUB_PR_NUMBER) must be set together"
            )
        if self.github_pr_number is not None:
            if self.revision_id is not None:
                raise ValueError("github_pr_number cannot be combined with revision_id")
            if self.bugzilla_needinfo_flag_id is not None:
                raise ValueError(
                    "github_pr_number cannot be combined with bugzilla_needinfo_flag_id"
                )
            if not self.comment:
                raise ValueError(
                    "comment (COMMENT) is required when github_pr_number is set, "
                    "to respond on the pull request"
                )
            return self
        if self.github_head_sha is not None or self.github_base_ref is not None:
            raise ValueError(
                "github_head_sha and github_base_ref require github_pr_number"
            )
        if self.bug_id is None:
            raise ValueError(
                "bug_id (BUG_ID) is required unless github_pr_number is set"
            )
        if self.bugzilla_needinfo_flag_id is not None:
            if self.revision_id is not None:
                raise ValueError(
                    "bugzilla_needinfo_flag_id cannot be combined with revision_id"
                )
            if not self.comment:
                raise ValueError(
                    "comment (COMMENT) is required when "
                    "bugzilla_needinfo_flag_id is set"
                )
        elif self.revision_id is not None and not self.comment:
            raise ValueError(
                "comment (COMMENT) is required when revision_id is set, to "
                "respond on the revision"
            )
        return self


def github_full_name(repo_url: str) -> str | None:
    """The ``owner/repo`` a github.com clone URL points at, or None for other hosts."""
    url = urlsplit(repo_url)
    if url.scheme not in ("http", "https") or url.hostname != "github.com":
        return None
    return url.path.strip("/").removesuffix(".git") or None


def check_pull_request_repo(github_repo: str, source_repo_url: str) -> None:
    """Refuse a PR from any repository but the agent's source.

    ``checkout_pull_request`` fetches the PR by number from the source, so a PR
    from another repository would silently check out an unrelated one.
    """
    source = github_full_name(source_repo_url)
    if source is None or source.lower() != github_repo.lower():
        raise RuntimeError(
            f"Pull request repository {github_repo} is not this agent's source "
            f"repository ({source_repo_url}); bug-fix can only work on its own "
            "repository's pull requests."
        )


async def main(ctx: HackbotContext) -> BugFixResult:
    inputs = AgentInputs()

    pull_request = None
    if inputs.github_repo is not None and inputs.github_pr_number is not None:
        check_pull_request_repo(inputs.github_repo, ctx.config.source.repo_url)
        merge_base = await checkout_pull_request(
            ctx,
            inputs.github_pr_number,
            inputs.github_base_ref or DEFAULT_BASE_REF,
            head_sha=inputs.github_head_sha,
        )
        pull_request = PullRequest(
            inputs.github_repo, inputs.github_pr_number, merge_base
        )
    elif inputs.revision_id:
        await checkout_revision(ctx, inputs.revision_id, inputs.broker_url)
    else:
        await ctx.prepare_repo()

    return await run_bug_fix(
        bugzilla_mcp_server={
            "type": "http",
            "url": inputs.bugzilla_mcp_url,
        },
        phabricator_mcp_server={
            "type": "http",
            "url": inputs.phabricator_mcp_url,
        },
        source_repo=ctx.repo_path,
        fx_ctx=ctx.firefox,
        bug=inputs.bug_id,
        revision_id=inputs.revision_id,
        comment=inputs.comment,
        bugzilla_needinfo_flag_id=inputs.bugzilla_needinfo_flag_id,
        pull_request=pull_request,
        model=inputs.model,
        max_turns=inputs.max_turns,
        effort=inputs.effort,
        log=ctx.log_path,
        verbose=True,
        actions_recorder=ctx.actions,
    )


if __name__ == "__main__":
    run_async(main)
