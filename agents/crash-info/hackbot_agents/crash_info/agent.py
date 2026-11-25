"""CrashInfo on Hackbot's harness: a Claude Agent SDK agent.

The model investigates through one in-process MCP server, ``crash_info``, whose
tools are CrashInfo's own (``react_tools.py``): ``triage``, the lookups (code,
changesets, crash-stats, Bugzilla, earlier fixed bugs), ``add_gap``,
``record_finding``, ``read_evidence`` and ``finish``. No built-in tool (Read,
Bash, ...) is offered: every lookup goes through the investigation's sources,
so it sees only what existed at the cutoff, counts against the lookup budget
and is stored as evidence that the answers cite.

    read the report, pin the code, complete the crash data (no model call)
    → recall earlier fixed bugs → triage → lookups and findings ⇄ → finish

The steps and their limits are the investigation's (``investigation.py``): the
other tools are refused until the triage of every taxonomy label is accepted;
``finish`` is refused while a gap has no answer; once the lookups or the turns
are spent, the agent is asked to record its answers and finish; a reply without
a tool call gets a reminder, twice at most.
"""

import asyncio
import logging
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk.types import SystemPromptFile
from hackbot_runtime import AgentError
from hackbot_runtime.claude import Reporter

from .investigation import (
    FINAL_TURNS,
    MAX_REMINDERS,
    REMINDERS,
    TRIAGE_TURNS,
    Investigation,
    conclude,
    new_investigation,
    open_report,
    recall_prompt,
    system_prompt,
)
from .models import GapStatus, LLMCallRecord
from .prompting import one_line
from .react_tools import Phase, ToolOutcome
from .report import ENGINE, build_report, render_markdown
from .results import CrashInfoOutcome, CrashInfoResult, Progress, build_result
from .schema import llm_schema
from .settings import Settings
from .sources import Sources, build_sources

__all__ = ["CrashInfoOutcome", "CrashInfoResult", "run_crash_info"]

log = logging.getLogger(__name__)

SERVER = "crash_info"
DEFAULT_MODEL = "claude-opus-5-5"
# Set explicitly: left unset, Opus 5.5 runs at `medium`.
DEFAULT_EFFORT = "high"
# The SDK's 1 MB default is too small for large messages.
MAX_BUFFER_SIZE = 10 * 1024 * 1024
TOOLS_NOTE = (
    "Your tools are those of the crash_info server, and only those: triage is "
    f"mcp__{SERVER}__triage, record_finding is mcp__{SERVER}__record_finding, and so "
    "on for every tool named above."
)
# The investigation's rules were first tuned with another model. Under them,
# Claude set aside 7 or 8 of the 13 labels at triage (that model: 1 to 4) and
# finished after 4 to 19 lookups (19 to 30), missing where some fixes belong. Its own guidance, and two steps
# sent back, hold it to the same depth: a triage that sets aside more than
# MAX_SET_ASIDE labels goes back once; finish goes back (twice at most) while
# answers are unsettled and fewer than MIN_LOOKUPS lookups (or half the budget)
# were used.
MAX_SET_ASIDE = 4
MIN_LOOKUPS = 15
CLAUDE_GUIDANCE = """How to investigate:
- Triage: set a label aside as not_applicable only when the report shows it plays \
no part in this crash, and say what in the report shows it. Information that would \
help a developer write the fix or test it is missing, even when the cause looks \
clear from the stack.
- Investigate before you infer. Most crashes need $floor to $lookups lookups. Before \
you answer which cases the fix must cover or where it belongs, find which caller on \
the crashing stack actually reaches the failing code, and read it; when several \
callers could, check each one.
- Confidence: high when the code and data you read point to one answer and you \
found no competing explanation; medium when an alternative remains plausible; low \
for a guess. Do not lower it only because no developer has confirmed the answer."""
OVER = "Not run: the investigation is over."
FINISHED_NOTE = "Write one closing sentence and call no more tools."
# Model errors that no retry or reminder can fix: a bad key, model or endpoint.
CONFIG_ERRORS = (401, 403, 404)


def tool_name(name: str) -> str:
    """The name the model calls a CrashInfo tool by."""
    return f"mcp__{SERVER}__{name}"


class Session:
    """The loop's state, shared by the tool handlers, which the SDK runs in this
    process, and the stream of the model's messages."""

    def __init__(self, inv: Investigation) -> None:
        self.inv = inv
        self.phase: Phase = "triage"
        self.turns = 0
        self.reminders = 0
        # A tool was called since the model's last reply ended.
        self.called = False
        # The reasoning the model gave for its current turn, kept with its lookups.
        self.thought = ""
        # The current turn's message: the CLI streams each of its blocks (text,
        # thinking, every tool call) as a message of its own, under one id.
        self.message_id: str | None = None
        self.texts: list[str] = []
        # What the tool calls of the current turn did.
        self.outcome = ToolOutcome()
        # Why the report phase began: budget_exhausted or turn_limit.
        self.limit: str | None = None
        self.stop_reason: str | None = None
        # Tool calls run one at a time: they share the budget and the state.
        self.lock = asyncio.Lock()
        # Steps sent back so far: "triage", "finish".
        self.sent_back: Counter[str] = Counter()

    @property
    def last_turn(self) -> int:
        """The turn at which the run stops, whatever the phase."""
        return TRIAGE_TURNS + 1 + self.inv.max_turns + FINAL_TURNS

    def on_message(self, message: AssistantMessage) -> bool:
        """A message of the main agent: a new turn, or another block of the
        current one. True when the run must stop at this turn."""
        if message.message_id is None or message.message_id != self.message_id:
            self.message_id = message.message_id
            return self.on_turn(message)
        # Another block of the same turn.
        self.add_thought(message)
        record = self.inv.info.llm_calls[-1]
        output = (message.usage or {}).get("output_tokens")
        if output is not None:
            record.output_tokens = max(record.output_tokens or 0, output)
        return False

    def add_thought(self, message: AssistantMessage) -> None:
        if text := thought_of(message):
            self.texts.append(text)
            self.thought = " ".join(self.texts)

    def on_turn(self, message: AssistantMessage) -> bool:
        self.turns += 1
        self.outcome = ToolOutcome()
        self.texts, self.thought = [], ""
        self.add_thought(message)
        info = self.inv.info
        usage = message.usage or {}
        info.llm_calls.append(
            LLMCallRecord(
                stage=f"react_{self.phase}",
                provider="anthropic",
                model=message.model,
                attempts=1,
                ok=message.error is None,
                input_tokens=_input_tokens(usage),
                output_tokens=usage.get("output_tokens"),
                error=str(message.error) if message.error else None,
            )
        )
        info.investigation_budget.llm_calls_used += 1
        self.inv.say(
            f"[{self.phase}] turn {self.turns}: {one_line(self.thought, 300) or '-'}"
        )
        if self.stop_reason is not None:  # a closing reply after finish
            return False
        # A triage sent back for Claude (send_back) gets its turn back.
        if (
            self.phase == "triage"
            and self.turns > TRIAGE_TURNS + self.sent_back["triage"]
        ):
            self.stop("no_triage", "no accepted triage")
        elif self.phase == "report" and self.turns > self.inv.max_turns + FINAL_TURNS:
            self.stop(self.limit or "turn_limit", "the turns for the answers are spent")
        elif self.turns > self.last_turn:
            self.stop(self.limit or "turn_limit", "the turns are spent")
        return self.stop_reason is not None

    async def call(self, name: str, args: dict[str, Any]) -> str:
        """Run one tool call and return what the model reads back."""
        async with self.lock:
            if self.stop_reason is not None and not self.outcome.finished:
                return OVER
            self.called = True
            if (back := self.send_back(name, args)) is not None:
                return back
            reply = await self.inv.toolbox.call(
                name, args, self.thought, self.phase, self.outcome
            )
            note = self.advance()
            return f"{reply}\n\n{note}" if note else reply

    @property
    def lookup_floor(self) -> int:
        """Lookups expected before finishing: MIN_LOOKUPS, or half the budget."""
        return min(MIN_LOOKUPS, self.inv.info.investigation_budget.max_tool_calls // 2)

    def send_back(self, name: str, args: dict[str, Any]) -> str | None:
        """A triage or a finish that stops short, sent back before it runs."""
        if name == "triage" and self.phase == "triage" and not self.sent_back[name]:
            labels = args.get("labels") if isinstance(args, dict) else None
            set_aside = {
                str(item.get("label")): str(item.get("reason") or "")
                for item in labels or []
                if isinstance(item, dict) and item.get("status") == "not_applicable"
            }
            if len(set_aside) > MAX_SET_ASIDE:
                self.sent_back[name] += 1
                self.inv.say(
                    f"[tools] triage sent back: {len(set_aside)} labels set aside"
                )
                listed = "\n".join(f"- {k}: {v}" for k, v in set_aside.items())
                return (
                    f"Triage not accepted yet: you set aside {len(set_aside)} labels "
                    f"as not applicable:\n{listed}\nA label is not applicable only "
                    "when the report shows it plays no part in this crash. Mark as "
                    "missing each one whose information would help a developer write "
                    "or test the fix, then call triage again with every label."
                )
        if (
            name == "finish"
            and self.phase == "investigate"
            and self.sent_back[name] < 2
        ):
            info = self.inv.info
            used = info.investigation_budget.tool_calls_used
            unsettled = [
                g.id for g in info.relevant_gaps() if g.status != GapStatus.RECOVERED
            ]
            if (
                info.mode.investigation
                and unsettled
                and used < self.lookup_floor
                and self.inv.toolbox.lookups_left > 0
            ):
                self.sent_back[name] += 1
                self.inv.say(f"[tools] finish sent back: {used} lookups used")
                return (
                    f"Not finished: you used {used} of your "
                    f"{info.investigation_budget.max_tool_calls} lookups, and these "
                    f"answers are not established: {', '.join(unsettled)}. Use more "
                    "lookups to establish them, the high-relevance ones first: read "
                    "the code each answer rests on, and check which caller on the "
                    "crashing stack reaches the failing code. Record what you find, "
                    "then call finish again."
                )
        return None

    def advance(self) -> str | None:
        """Move between steps after a tool call; returns what the model is told."""
        if self.outcome.finished:
            if self.stop_reason is None:
                self.stop_reason = (
                    "finished"
                    if self.phase == "investigate"
                    else self.limit or "finished"
                )
                return FINISHED_NOTE
            return None
        if self.phase == "triage" and self.outcome.triaged:
            self.phase = "investigate"
        elif self.phase == "investigate":
            info, toolbox = self.inv.info, self.inv.toolbox
            limit = why = None
            if info.mode.investigation and toolbox.lookups_left <= 0:
                limit, why = "budget_exhausted", "The lookup budget is spent."
            elif self.turns >= self.inv.max_turns:
                limit, why = "turn_limit", "The investigation has used its turns."
            if limit:
                self.inv.say(f"[tools] {why} Asking for the remaining answers.")
                self.phase, self.limit = "report", limit
                return (
                    f"{why} Record your answers for the gaps still without one "
                    "(record_finding), from the evidence gathered so far, then call "
                    "finish."
                )
        return None

    def on_result(self, result: ResultMessage, model: str) -> None:
        """The end of one reply of the model."""
        if not result.is_error or self.stop_reason is not None:
            return
        if result.api_error_status in CONFIG_ERRORS:
            raise AgentError(
                f"model {model}: {result.result or result.errors or result.subtype}"
            )
        if result.subtype == "error_max_turns":
            self.stop(self.limit or "turn_limit", "the turns are spent")
        else:
            detail = one_line(
                str(result.result or result.errors or result.subtype), 300
            )
            self.stop("model_error", f"the model call failed: {detail}")

    def ended_without_finishing(self) -> str | None:
        """The model's reply ended before ``finish``: the reminder to send, or
        None once it has had its reminders."""
        if self.called:
            self.reminders = 0
        self.called = False
        self.reminders += 1
        if self.reminders > MAX_REMINDERS:
            self.stop(
                "no_triage"
                if self.phase == "triage"
                else self.limit or "agent_stopped",
                f"the agent stopped calling tools ({self.phase})",
            )
            return None
        return REMINDERS[self.phase]

    def stop(self, reason: str, why: str) -> None:
        if self.stop_reason is None:
            self.stop_reason = reason
            self.inv.say(f"[stop] {why}")


def thought_of(message: AssistantMessage) -> str:
    """The reasoning behind a turn: its text, else the tail of its thinking."""
    text = " ".join(b.text for b in message.content if isinstance(b, TextBlock)).strip()
    if text:
        return text
    thinking = " ".join(
        b.thinking for b in message.content if isinstance(b, ThinkingBlock)
    ).strip()
    return "…" + thinking[-600:] if len(thinking) > 600 else thinking


def _input_tokens(usage: dict[str, Any]) -> int | None:
    """Every input token of a call, cached or not."""
    parts = [
        usage.get(k)
        for k in (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    ]
    return sum(p for p in parts if p) if any(p is not None for p in parts) else None


def mcp_server(session: Session):
    """The ``crash_info`` MCP server: one tool per CrashInfo tool of this run."""
    toolbox = session.inv.toolbox

    def handler(name: str):
        async def run(args: dict[str, Any]) -> dict[str, Any]:
            text = await session.call(name, args)
            return {"content": [{"type": "text", "text": text}]}

        return run

    tools = [
        tool(t.name, toolbox.describe(t), llm_schema(t.args))(handler(t.name))
        for t in toolbox.tools.values()
    ]
    return create_sdk_mcp_server(name=SERVER, tools=tools)


def write_system_prompt(prompt: str, directory: Path) -> SystemPromptFile:
    """Write ``prompt`` for the CLI to read with ``--system-prompt-file``: the
    taxonomy makes it long for a single command-line argument."""
    path = directory / "system.md"
    path.write_text(prompt, encoding="utf-8")
    return {"type": "file", "path": str(path)}


def agent_options(
    session: Session, workdir: Path, model: str, effort: str | None
) -> ClaudeAgentOptions:
    guidance = CLAUDE_GUIDANCE.replace("$floor", str(session.lookup_floor)).replace(
        "$lookups", str(session.inv.info.investigation_budget.max_tool_calls)
    )
    prompt = f"{system_prompt(session.inv)}\n\n{guidance}\n\n{TOOLS_NOTE}"
    return ClaudeAgentOptions(
        system_prompt=write_system_prompt(prompt, workdir),
        mcp_servers={SERVER: mcp_server(session)},
        strict_mcp_config=True,
        # No built-in tool: the lookups are CrashInfo's, bound to the cutoff.
        tools=[],
        allowed_tools=[tool_name(name) for name in session.inv.toolbox.tools],
        permission_mode="dontAsk",
        # An empty directory: there is nothing to read in it.
        cwd=str(workdir),
        model=model,
        **({"effort": effort} if effort else {}),
        # The CLI counts its turns more finely than the session (about twice as
        # many): the session's own limits stop the run, this only bounds it.
        max_turns=3 * (session.last_turn + MAX_REMINDERS),
        setting_sources=[],
        max_buffer_size=MAX_BUFFER_SIZE,
    )


async def converse(
    session: Session,
    client: Any,
    opening: str,
    reporter: Reporter,
    model: str,
) -> float | None:
    """Drive the conversation until ``finish``, a limit or the reminders are
    spent; returns the session's cost as the SDK reports it."""
    cost: float | None = None
    prompt: str | None = opening
    while prompt is not None:
        await client.query(prompt)
        async for message in client.receive_response():
            reporter.message(message)
            if isinstance(message, AssistantMessage):
                if message.parent_tool_use_id is not None:
                    continue
                if session.on_message(message):
                    await client.interrupt()
            elif isinstance(message, ResultMessage):
                if message.total_cost_usd is not None:
                    cost = message.total_cost_usd
                session.on_result(message, model)
        prompt = None if session.stop_reason else session.ended_without_finishing()
    return cost


async def run_crash_info(
    bug_id: int,
    settings: Settings,
    *,
    as_of: datetime | None = None,
    before_first_patch: bool = False,
    model: str = DEFAULT_MODEL,
    effort: str | None = DEFAULT_EFFORT,
    sources: Sources | None = None,
    run_id: str | None = None,
    progress: Progress | None = None,
    log_path: Path | None = None,
    verbose: bool = False,
    client_factory: Any = ClaudeSDKClient,
) -> CrashInfoOutcome:
    """Investigate one bug as it is now, as of ``as_of``, or as it was one second
    before its first patch. ``client_factory`` stands in for ``ClaudeSDKClient``
    in tests."""
    own_sources = sources is None
    sources = sources or build_sources(settings.sources)
    # Through a router the model already names its provider (anthropic/claude-…).
    llm = model if "/" in model else f"anthropic/{model}"
    inv = new_investigation(
        bug_id,
        settings,
        sources,
        as_of=as_of,
        before_first_patch=before_first_patch,
        run_id=run_id,
        llm=llm,
        say=progress or log.info,
    )
    session = Session(inv)
    cost: float | None = None
    try:
        prompts = await open_report(inv)
        if recalled := await recall_prompt(inv):
            prompts.append(recalled)
        with (
            tempfile.TemporaryDirectory(prefix="crash-info-") as workdir,
            Reporter(verbose=verbose, log_path=log_path) as reporter,
        ):
            options = agent_options(session, Path(workdir), model, effort)
            reporter.header(f"bug {bug_id}")
            async with client_factory(options=options) as client:
                cost = await converse(
                    session, client, "\n\n".join(prompts), reporter, model
                )
    finally:
        if own_sources:
            await sources.aclose()
    conclude(inv, session.stop_reason or "agent_stopped")
    report = build_report(inv.info, engine=ENGINE)
    return CrashInfoOutcome(
        state=inv.info,
        report=report,
        markdown=render_markdown(inv.info),
        result=build_result(
            inv.info,
            report,
            llm=llm,
            num_turns=session.turns,
            cost_usd=cost,
        ),
    )
