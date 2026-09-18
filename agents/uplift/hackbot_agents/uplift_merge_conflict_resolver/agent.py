"""Uplift conflict-resolution agent.

A single-stage claude-agent-sdk agent that reproduces a failed uplift
cherry-pick on a stable branch and resolves the conflicts. The entrypoint
checks the tree out and the runtime collects the resolved commits into
``changes.patch``; this module orchestrates the session and reports what
happened for human review.
"""

import json
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path

from agent_tools import mozilla_vcs, searchfox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.mozilla_vcs import MozillaVcsContext
from agent_tools.searchfox import SearchfoxContext
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpServerConfig,
    ResultMessage,
)
from hackbot_runtime import AgentError, HackbotAgentResult
from hackbot_runtime.claude import Reporter
from phabricator_client import PhabricatorClient, PhabricatorSettings
from searchfox import AsyncSearchfoxClient

from .config import (
    MODEL,
    ConflictReport,
    FetchedDiff,
    Report,
    RequestedSource,
    UpliftSource,
)
from .verify import head_commit, verify_uplift

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent

# Where the broker mounts its read-only Conduit proxy.
PROXY_MOUNT = "/phabricator"

# Not a secret: the proxy discards it and substitutes the real Conduit key.
# Sized to the 32 characters `PhabricatorSettings` requires.
PROXY_API_TOKEN = "hackbot-broker-proxy-placeholder"


class UpliftResult(HackbotAgentResult):
    """Outcome of an uplift run, saved to ``summary.json`` under ``findings``.

    The patch itself is collected separately into ``changes.patch``.
    """

    # The stable branch the patches were uplifted onto.
    target_branch: str

    # The commit they were applied onto. A branch name moves, so this is what
    # makes the run reproducible.
    base_commit: str = ""

    # Originating Bugzilla bug, when one was provided as context.
    bug_id: int | None = None

    # The sources as requested, in order, and what each resolved to.
    requested_sources: list[RequestedSource] = []

    # Whether every source applied and every conflict was resolved.
    resolved: bool = False

    # The agent's own grade: high/medium/low.
    confidence: str | None = None

    # Developer-facing summary of what conflicted and how it was resolved.
    summary: str = ""

    conflicts: list[ConflictReport] = []
    unresolved: list[str] = []

    # What the checks found wrong regardless of what the agent claimed.
    # Non-empty means `resolved` was forced to `False`.
    verification_failures: list[str] = []


async def run_uplift(
    *,
    bugbug_mcp_server: McpServerConfig,
    broker_url: str,
    source_repo: Path,
    target_branch: str,
    sources: list[UpliftSource],
    target_commit: str | None = None,
    bug_id: int | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    effort: str | None = None,
    verbose: bool = False,
    log: Path | None = None,
    publish_file: Callable[[str, Path, str | None], str] | None = None,
) -> UpliftResult:
    """Resolve the uplift merge conflicts for ``sources`` onto ``target_branch``.

    Returns an :class:`UpliftResult`; raises :class:`AgentError` if the agent
    ends in an error or produces no result message.
    """
    if not sources:
        raise AgentError("no sources to uplift")

    logger.info("resolving uplift of %d source(s) onto %s", len(sources), target_branch)

    # Where the agent starts: what the checks compare against afterwards.
    base_commit = head_commit(source_repo)
    if target_commit and base_commit != target_commit:
        raise AgentError(
            f"checkout is at {base_commit}, not the requested {target_commit}"
        )

    scratch_dir = Path(tempfile.mkdtemp(prefix="uplift-"))
    scratch_out = scratch_dir / "out"
    scratch_out.mkdir(parents=True, exist_ok=True)

    # Materialize the diffs first, so the prompt names the paths they landed at.
    fetched = await fetch_source_diffs(
        build_phabricator_client(broker_url), sources, scratch_out
    )
    user_prompt = build_user_prompt(
        target_branch=target_branch,
        sources=sources,
        bug_id=bug_id,
        scratch_out=scratch_out,
        fetched=fetched,
    )
    options = build_options(
        system_prompt=load_system_prompt(),
        source_repo=source_repo,
        scratch_out=scratch_out,
        mcp_servers=build_mcp_servers(bugbug_mcp_server),
        model=model,
        effort=effort,
        max_turns=max_turns,
    )

    with Reporter(verbose=verbose, log_path=log) as reporter:
        reporter.header(f"uplift onto {target_branch}")
        result_msg = await run_session(reporter, options, user_prompt)

    check_result(result_msg, target_branch)

    report = read_agent_report(scratch_out, publish_file)

    # `Report`'s fields are exactly the result's report-derived ones.
    report_fields = report.model_dump()
    failures = verify_uplift(source_repo, base_commit, report)
    if failures:
        logger.warning(
            "uplift onto %s did not pass verification: %s",
            target_branch,
            "; ".join(failures),
        )
        report_fields["resolved"] = False

    result = UpliftResult(
        target_branch=target_branch,
        base_commit=base_commit,
        bug_id=bug_id,
        requested_sources=describe_requested(sources, fetched),
        num_turns=result_msg.num_turns,
        total_cost_usd=result_msg.total_cost_usd,
        verification_failures=failures,
        **report_fields,
    )
    publish_verified_report(scratch_out, publish_file, result)
    return result


def describe_requested(
    sources: list[UpliftSource], fetched: list[FetchedDiff | None]
) -> list[RequestedSource]:
    """What each requested source resolved to, in order.

    Reads the lists the prompt was rendered from, so it names the diff that was
    actually fetched -- which an unpinned source does not.
    """
    return [
        RequestedSource(
            source=source.model_dump(),
            diff_id=diff.diff_id if diff is not None else None,
            base_commit=diff.base_commit if diff is not None else None,
            author=diff.author if diff is not None else None,
        )
        for source, diff in zip(sources, fetched, strict=True)
    ]


def build_mcp_servers(
    bugbug_mcp_server: McpServerConfig,
) -> dict[str, McpServerConfig]:
    """The bugbug server, plus in-process Searchfox and HGMO lookups.

    No Firefox build tools: resolving a conflict is a source-level judgment,
    and a build would need the toolchain image `build-repair` carries.
    """
    return {
        "bugbug": bugbug_mcp_server,
        "searchfox": build_sdk_server(
            "searchfox",
            SearchfoxContext(client=AsyncSearchfoxClient()),
            searchfox.TOOLS,
        ),
        "mozilla_vcs": build_sdk_server(
            "mozilla_vcs", MozillaVcsContext(), mozilla_vcs.TOOLS
        ),
    }


def build_options(
    *,
    system_prompt: str,
    source_repo: Path,
    scratch_out: Path,
    mcp_servers: dict[str, McpServerConfig],
    model: str | None = None,
    effort: str | None = None,
    max_turns: int | None = None,
) -> ClaudeAgentOptions:
    """Assemble the agent's SDK options to mirror a local Claude Code session.

    The container is the sandbox, so the agent runs unattended with every
    built-in tool; only `AskUserQuestion` goes, since nobody can answer.
    `setting_sources` loads `project` for the checkout's own `CLAUDE.md` and
    in-tree skills, but not `local`, which a fresh clone cannot have.
    """
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model or MODEL,
        cwd=str(source_repo),
        add_dirs=[str(scratch_out)],
        mcp_servers=mcp_servers,
        disallowed_tools=["AskUserQuestion"],
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        setting_sources=["project"],
        # Omitted rather than defaulted, as in `bug-fix`: the API's own default
        # effort is `high`, so naming it here would only duplicate it.
        **({"effort": effort} if effort else {}),
    )


def load_system_prompt() -> str:
    """How to resolve an uplift, which is the same for every run.

    Deliberately free of per-run detail: that belongs in the task, and it keeps
    this an identical prefix across runs for prompt caching to reuse.
    """
    return (HERE / "prompts" / "system.md").read_text()


def build_user_prompt(
    *,
    target_branch: str,
    sources: list[UpliftSource],
    bug_id: int | None,
    scratch_out: Path,
    fetched: list[FetchedDiff | None],
) -> str:
    """The run's own task: what to uplift, onto what, and where to report it."""
    template = (HERE / "prompts" / "task.md").read_text()
    return template.format(
        target_branch=target_branch,
        sources_block=render_sources(sources, fetched),
        bug_block=render_bug_block(bug_id),
        scratch_out=str(scratch_out),
    )


def render_sources(
    sources: list[UpliftSource], fetched: list[FetchedDiff | None]
) -> str:
    """Describe each source as a numbered work item, in order.

    ``fetched`` is positional: one entry per source, ``None`` for a kind with
    nothing to fetch.
    """
    return "\n".join(
        source.render_work_item(index, diff)
        for index, (source, diff) in enumerate(
            zip(sources, fetched, strict=True), start=1
        )
    )


def render_bug_block(bug_id: int | None) -> str:
    """The optional originating-bug section of the prompt; empty when no bug."""
    if bug_id is None:
        return ""
    return (
        f"## Originating bug\n\n"
        f"These patches belong to bug {bug_id}. Consult it with `get_bugzilla_bug` "
        f"when a conflict's intent is unclear.\n\n"
    )


def build_phabricator_client(broker_url: str) -> PhabricatorClient:
    """A Conduit client pointed at the broker's read-only proxy."""
    return PhabricatorClient(
        PhabricatorSettings(
            url=f"{broker_url.rstrip('/')}{PROXY_MOUNT}",
            api_key=PROXY_API_TOKEN,
        )
    )


async def fetch_source_diffs(
    client: PhabricatorClient,
    sources: list[UpliftSource],
    scratch_out: Path,
) -> list[FetchedDiff | None]:
    """Materialize whatever each source needs on disk, one entry per source.

    A git source is cherry-picked from the repo and contributes ``None``.
    """
    fetched: list[FetchedDiff | None] = []
    for source in sources:
        diff = await source.fetch_diff(client, scratch_out)
        if diff is not None:
            logger.info("fetched %s", diff.path.name)
        fetched.append(diff)
    return fetched


async def run_session(
    reporter: Reporter, options: ClaudeAgentOptions, prompt: str
) -> ResultMessage | None:
    """Drive one agent session to completion and return its result message.

    Separate from :func:`run_uplift` so a test can stand in for the session.
    """
    result_msg: ResultMessage | None = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            reporter.message(msg)
            if isinstance(msg, ResultMessage):
                result_msg = msg
    return result_msg


def check_result(result_msg: ResultMessage | None, target_branch: str) -> None:
    """Raise :class:`AgentError` if the agent produced no result or errored."""
    if result_msg is None:
        raise AgentError(f"uplift onto {target_branch}: agent produced no result")
    if result_msg.is_error:
        raise AgentError(
            f"uplift onto {target_branch} failed: "
            f"{result_msg.result or result_msg.subtype}"
        )


def read_agent_report(
    scratch_out: Path,
    publish_file: Callable[[str, Path, str | None], str] | None,
) -> Report:
    """Parse the report the agent wrote, publishing it as it wrote it.

    Published as ``report.unverified.json``, since its `resolved` is the
    agent's claim and the checks may overrule it. A missing or unparseable
    report is an unresolved run rather than a crash; the patch is collected
    separately and is worth surfacing either way.
    """
    summary = scratch_out / "summary.md"
    if publish_file is not None and summary.exists():
        publish_file("summary.md", summary, "text/markdown")

    written = scratch_out / "report.json"
    if not written.exists():
        logger.warning("agent wrote no report.json")
        return Report()
    if publish_file is not None:
        publish_file("report.unverified.json", written, "application/json")
    try:
        return Report.model_validate_json(written.read_text())
    except ValueError as exc:
        logger.warning("report.json did not validate: %s", exc)
        return Report()


def publish_verified_report(
    scratch_out: Path,
    publish_file: Callable[[str, Path, str | None], str] | None,
    result: UpliftResult,
) -> None:
    """Publish ``report.json``, whose `resolved` is the one the checks allow.

    This is the report a consumer reads, so it has to agree with the run
    summary rather than repeat a claim the checks rejected. The agent's own
    file stays published beside it, unchanged.
    """
    if publish_file is None:
        return
    body = result.model_dump(
        include={
            "resolved",
            "confidence",
            "summary",
            "conflicts",
            "unresolved",
            "verification_failures",
        }
    )
    path = scratch_out / "report.verified.json"
    path.write_text(json.dumps(body, indent=2))
    publish_file("report.json", path, "application/json")
