"""Example Codex agent: summarise a Bugzilla bug and record the summary as a comment."""

import logging
import os
from pathlib import Path

import bugsy
from agent_tools import bugzilla
from agent_tools.codex_sdk import build_mcp_server, mcp_servers_config
from hackbot_runtime import ActionsRecorder, AgentError, HackbotAgentResult, openai_wif
from hackbot_runtime.actions.codex_sdk import actions_server_for
from hackbot_runtime.codex import Reporter
from openai_codex import ApprovalMode, AsyncCodex, Sandbox
from openai_codex.generated.v2_all import ReasoningEffort

from .config import BUGZILLA_READ_TOOLS, ENABLED_ACTION_TYPES

HERE = Path(__file__).resolve().parent

logger = logging.getLogger("example-codex")


class ExampleCodexResult(HackbotAgentResult):
    summary: str | None = None


def load_system_prompt() -> str:
    return (HERE / "prompts" / "system.md").read_text()


def build_user_prompt(bug_id: int) -> str:
    return (
        f"Summarise bug {bug_id} for someone seeing it for the first time: what "
        "is broken, where, and what has been tried so far. Keep it to a few "
        "sentences, record it as a comment on the bug, then reply with the "
        "same summary."
    )


async def run_example_codex(
    *,
    bug_id: int,
    model: str | None = None,
    effort: str | None = None,
    verbose: bool = False,
    log: Path | None = None,
    actions_recorder: ActionsRecorder | None = None,
) -> ExampleCodexResult:
    """Summarise ``bug_id`` and record the summary as a comment on it."""
    subject = f"bug {bug_id}"
    logger.info("summarising %s", subject)

    bugzilla_server = build_mcp_server(
        "bugzilla",
        bugzilla.BugzillaContext(client=bugsy.Bugsy()),
        [t for t in bugzilla.TOOLS if t.name in BUGZILLA_READ_TOOLS],
    )
    actions_recorder, actions_server = actions_server_for(
        actions_recorder, types=ENABLED_ACTION_TYPES
    )

    with Reporter(verbose=verbose, log_path=log) as reporter:
        reporter.header(subject)
        # Start the servers before Codex so they outlive it.
        async with (
            bugzilla_server.serve(),
            actions_server.serve(),
            AsyncCodex() as codex,
        ):
            if not openai_wif.is_enabled():
                await codex.login_api_key(os.environ["OPENAI_API_KEY"])
            thread = await codex.thread_start(
                model=model,
                developer_instructions=load_system_prompt(),
                config=mcp_servers_config(bugzilla_server, actions_server),
                approval_mode=ApprovalMode.deny_all,
                sandbox=Sandbox.read_only,
                ephemeral=True,
            )
            await reporter.session(thread)
            turn = await thread.turn(
                build_user_prompt(bug_id),
                effort=ReasoningEffort(effort) if effort else None,
            )
            async for event in turn.stream():
                reporter.message(event)

    result = reporter.result
    if result is None:
        raise AgentError(f"{subject}: turn ended without completing")
    if result.is_error:
        raise AgentError(f"{subject}: summary failed: {result.error}")
    if not actions_recorder.actions:
        raise AgentError(f"{subject}: agent finished without recording a comment")

    return ExampleCodexResult(summary=result.result, num_turns=result.num_turns)
