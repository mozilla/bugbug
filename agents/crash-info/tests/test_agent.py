"""The Claude Agent SDK engine (agent.py), offline: a scripted Claude and a fake
Mozilla.

``FakeClaude`` stands in for ``ClaudeSDKClient``: it plays scripted turns and
calls the ``crash_info`` MCP server's tools the way the CLI does, over MCP (in
memory), so the tools, their schemas and their replies are the real ones.
"""

import itertools
import re
from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("claude_agent_sdk")
pytest.importorskip("hackbot_runtime")

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from hackbot_runtime import AgentError  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from hackbot_agents.crash_info import agent  # noqa: E402
from hackbot_agents.crash_info.__main__ import AgentInputs  # noqa: E402
from hackbot_agents.crash_info.agent import (  # noqa: E402
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    SERVER,
    TOOLS_NOTE,
    run_crash_info,
    tool_name,
)
from hackbot_agents.crash_info.investigation import REMINDERS  # noqa: E402
from hackbot_agents.crash_info.models import GapStatus  # noqa: E402
from hackbot_agents.crash_info.report import render_handoff  # noqa: E402
from hackbot_agents.crash_info.taxonomy import load_taxonomy  # noqa: E402

from .conftest import BUG, CUTOFF, NEW_REV, OLD_REV  # noqa: E402

TAXONOMY = load_taxonomy()
COST = 0.25
SET_ASIDE, LOOKUPS = agent.MAX_SET_ASIDE, agent.MIN_LOOKUPS


@pytest.fixture(autouse=True)
def lenient(monkeypatch):
    """The scripted runs set aside 8 labels and look up little: the steps sent
    back for Claude are tested on their own, with their real thresholds."""
    monkeypatch.setattr(agent, "MAX_SET_ASIDE", 13)
    monkeypatch.setattr(agent, "MIN_LOOKUPS", 0)


_ids = itertools.count(1)

Calls = list[tuple[str, dict[str, Any]]]
# A scripted turn: what the model says and the tools it calls; no call ends the
# reply. Or a function of everything the model has read so far that returns one;
# or the "error" a failed model call ends the reply with.
Turn = tuple[str, Calls] | Callable[[list[str]], tuple[str, Calls]] | ResultMessage


class FakeClaude:
    """Replays ``script`` for every query, like the CLI: assistant messages, then
    the tool results, then a result message ending each reply."""

    def __init__(self, script: list[Turn]) -> None:
        self.script = script
        self.read: list[str] = []  # prompts and tool results, in order
        self.prompts: list[str] = []
        self.options: Any = None
        self.system = ""
        self.interrupted = False
        self.tools: list[Any] = []

    def __call__(self, *, options: Any) -> "FakeClaude":
        self.options = options
        with open(options.system_prompt["path"], encoding="utf-8") as fh:
            self.system = fh.read()  # a file in a directory removed after the run
        return self

    async def __aenter__(self) -> "FakeClaude":
        server = self.options.mcp_servers[SERVER]["instance"]
        self._mcp = create_connected_server_and_client_session(server)
        self.session = await self._mcp.__aenter__()
        self.tools = (await self.session.list_tools()).tools
        return self

    async def __aexit__(self, *exc: object) -> None:
        # Closed cleanly whatever ended the run, like ClaudeSDKClient: the error,
        # if any, propagates from the run itself.
        await self._mcp.__aexit__(None, None, None)

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)
        self.read.append(prompt)

    async def interrupt(self) -> None:
        self.interrupted = True

    async def receive_response(self):
        while True:
            assert self.script, "the agent asked for more turns than scripted"
            step = self.script.pop(0)
            if isinstance(step, ResultMessage):
                yield step
                return
            thought, calls = step(self.read) if callable(step) else step
            ids = [f"toolu_{next(_ids)}" for _ in calls]
            # Like the CLI: one message per block (the text, then each tool
            # call), under the id of the model's message.
            message_id = f"msg_{next(_ids)}"
            blocks = [
                TextBlock(thought),
                *(
                    ToolUseBlock(id=i, name=tool_name(name), input=args)
                    for i, (name, args) in zip(ids, calls)
                ),
            ]
            for n, block in enumerate(blocks):
                yield AssistantMessage(
                    content=[block],
                    model="claude-test",
                    message_id=message_id,
                    usage={
                        "input_tokens": 100,
                        "cache_read_input_tokens": 900,
                        "output_tokens": 10 * (n + 1),
                    },
                )
            if self.interrupted:
                yield result(is_error=True, subtype="error_during_execution")
                return
            if not calls:
                yield result()
                return
            blocks = []
            for i, (name, args) in zip(ids, calls):
                reply = await self.session.call_tool(name, args)
                text = reply.content[0].text
                self.read.append(text)
                blocks.append(ToolResultBlock(tool_use_id=i, content=text))
            yield UserMessage(content=blocks)


def result(**fields: Any) -> ResultMessage:
    defaults: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "session_id": "fake",
        "total_cost_usd": COST,
    }
    return ResultMessage(**(defaults | fields))


def call(name: str, **args: Any) -> tuple[str, dict[str, Any]]:
    return (name, args)


def triage(missing: dict[str, tuple[str, str]], only: int | None = None):
    """A triage of every label (or of the first ``only``): the ``missing`` ones as
    gaps, Obj.STATE as already in the report, the rest as not applicable."""
    items = []
    for label in TAXONOMY.ids[:only]:
        if label in missing:
            relevance, question = missing[label]
            items.append(
                {
                    "label": label,
                    "status": "missing",
                    "question": question,
                    "relevance": relevance,
                    "why_it_matters": "the fix depends on it",
                }
            )
        elif label == "Obj.STATE":
            items.append(
                {
                    "label": label,
                    "status": "in_report",
                    "reason": "the report says mWidget seems null",
                    "evidence_ids": ["ev-003"],  # comment 0
                    "quote": "`mWidget` seems to be null?",
                }
            )
        else:
            items.append(
                {
                    "label": label,
                    "status": "not_applicable",
                    "reason": "nothing in this crash depends on it",
                }
            )
    return call("triage", labels=items)


MISSING = {
    "Obj.LIFETIME": ("high", "Is mWidget still alive when Foo::Bar runs?"),
    "Exec.SYSTEM": ("medium", "In which shutdown phase does Foo::Bar crash?"),
    "Verif.EXPECTED": ("medium", "What must Foo::Bar do once mWidget is gone?"),
    "Trigger.SEQUENCE": ("low", "Which events make Foo::Bar run after teardown?"),
}


def evidence_id(read: list[str], marker: str) -> str:
    """The id of the first observed evidence item whose header contains ``marker``."""
    for text in read:
        for line in text.splitlines():
            if line.startswith("[ev-") and marker in line:
                return re.match(r"\[(ev-\d+)\]", line).group(1)
    raise AssertionError(f"no observed evidence about {marker!r}")


async def run(settings, sources, claude: FakeClaude, **kwargs):
    return await run_crash_info(
        BUG,
        settings,
        sources=sources,
        progress=lambda m: None,
        client_factory=claude,
        **kwargs,
    )


async def test_claude_triages_looks_up_records_and_finishes(
    settings, sources, tmp_path
):
    def findings(read):
        code = evidence_id(read, "dom/foo/Foo.cpp lines")
        stats = evidence_id(read, "crash-stats for")
        return (
            "Recording what the evidence shows.",
            [
                call(
                    "record_finding",
                    gap_id="gap-001",
                    status="established",
                    answer="Foo::Bar dereferences mWidget without checking it "
                    "(mWidget->Paint()).",
                    evidence_ids=[code],
                    confidence="high",
                ),
                call(
                    "record_finding",
                    gap_id="gap-002",
                    status="inferred",
                    answer="At shutdown (profile-before-change), once mWidget is gone.",
                    evidence_ids=[stats],
                    confidence="high",
                    reasoning="The crash-stats shutdown phase says so.",
                ),
                call(
                    "record_finding",
                    gap_id="gap-003",
                    status="inferred",
                    answer="It must not touch mWidget after nsWindow::Destroy.",
                    evidence_ids=[code],
                    confidence="medium",
                ),
                call("record_finding", gap_id="gap-004", status="unknown"),
            ],
        )

    claude = FakeClaude(
        [
            # A lookup before the triage is refused.
            (
                "Read the code first.",
                [call("read_code", gap_id="gap-001", file="dom/foo/Foo.cpp", line=42)],
            ),
            ("Triage.", [triage(MISSING)]),
            (
                "The crash is a null mWidget in Foo::Bar: read the function.",
                [
                    call(
                        "read_code", gap_id="gap-001", file="dom/foo/Foo.cpp", line=42
                    ),
                    call(
                        "crash_stats", gap_id="gap-002", signature="mozilla::Foo::Bar"
                    ),
                ],
            ),
            (
                "Was Foo.cpp changed after the cutoff?",
                [call("read_changeset", gap_id="gap-001", node=NEW_REV)],
            ),
            ("Done.", [call("finish")]),
            findings,
            ("Finishing.", [call("finish", summary="mWidget is null at shutdown.")]),
            ("mWidget is null at shutdown in Foo::Bar.", []),
        ]
    )
    outcome = await run(
        settings, sources, claude, before_first_patch=True, log_path=tmp_path / "log"
    )
    state, report, read = outcome.state, outcome.report, "\n".join(claude.read)

    assert state.as_of == CUTOFF and state.source_revision == OLD_REV
    assert report["engine"] == "claude-agent-sdk"
    assert report["stop_reason"] == "finished"
    assert not claude.script and not claude.interrupted
    # The fix and everything after the cutoff stay invisible to the model.
    for leaked in ("SECRET_FIX_DETAIL", "Null-check mWidget", "FIXED_LINE", "1000002"):
        assert leaked not in read, leaked

    # Hackbot's harness, restricted to CrashInfo's tools: no built-in tool, every
    # tool of the crash_info server allowed, and nothing else.
    options = claude.options
    assert options.tools == [] and options.setting_sources == []
    assert options.model == DEFAULT_MODEL and options.effort == DEFAULT_EFFORT
    assert options.permission_mode == "dontAsk" and options.strict_mcp_config
    names = {t.name for t in claude.tools}
    assert {"triage", "read_code", "crash_stats", "record_finding", "finish"} <= names
    assert sorted(options.allowed_tools) == sorted(tool_name(n) for n in names)
    read_code = next(t for t in claude.tools if t.name == "read_code")
    assert "Usually serves: Repair.GUARD" in read_code.description
    assert read_code.inputSchema["required"] == ["gap_id", "file"]
    assert "Obj.LIFETIME (Object lifetime" in claude.system
    assert TOOLS_NOTE in claude.system

    # One opening message: the report, then the earlier fixed bugs.
    (opening,) = claude.prompts
    assert "Earlier bugs:" in opening and "earlier bug 999999" in opening
    assert "Not available yet: start with triage" in read

    # Each lookup served a gap, with the reasoning of its turn; the post-cutoff
    # changeset was refused.
    steps = [a for a in state.actions_taken if a.arguments["tool"] != "recall"]
    assert [(a.arguments["tool"], a.gap_id) for a in steps] == [
        ("read_code", "gap-001"),
        ("crash_stats", "gap-002"),
        ("read_changeset", "gap-001"),
    ]
    assert steps[0].rationale.startswith("The crash is a null mWidget")
    assert steps[-1].outcome == "blocked"
    # finish was refused while gaps had no answer, then accepted.
    assert "gap-001 [Obj.LIFETIME] has no recorded answer" in read
    assert "Write one closing sentence and call no more tools." in read
    gaps = {g.id: g for g in state.relevant_gaps()}
    assert gaps["gap-001"].status == GapStatus.RECOVERED
    assert gaps["gap-002"].inference.confidence.value == "high"

    # Accounting: every model turn, its tokens (cached ones included), the cost.
    assert len(state.llm_calls) == 8 and outcome.result.num_turns == 8
    assert state.llm_calls[0].provider == "anthropic"
    assert state.llm_calls[0].input_tokens == 1000
    assert state.llm_calls[2].output_tokens == 30  # the text and two tool calls
    assert outcome.result.total_cost_usd == COST
    assert outcome.result.llm == f"anthropic/{DEFAULT_MODEL}"
    # The conversation is in Hackbot's log; the notes for bug-fix render.
    log = (tmp_path / "log").read_text()
    assert "mcp__crash_info__triage" in log and "[done]" in log
    assert "Established:" in render_handoff(state)


async def test_a_reply_without_tool_calls_gets_two_reminders(settings, sources):
    claude = FakeClaude(
        [("I will triage.", []), ("Soon.", []), ("Still thinking.", [])]
    )
    outcome = await run(settings, sources, claude)
    assert outcome.report["stop_reason"] == "no_triage"
    assert claude.prompts[1:] == [REMINDERS["triage"], REMINDERS["triage"]]


async def test_a_spent_budget_asks_for_the_answers(settings, sources):
    settings.investigation.max_tool_calls = 1
    claude = FakeClaude(
        [
            ("Triage.", [triage(MISSING)]),
            (
                "Read the function.",
                [call("read_code", gap_id="gap-001", file="dom/foo/Foo.cpp", line=42)],
            ),
            (
                "Answering from what I have.",
                [
                    *(
                        call("record_finding", gap_id=f"gap-00{n}", status="unknown")
                        for n in range(1, 5)
                    ),
                    call("finish"),
                ],
            ),
            ("Done.", []),
        ]
    )
    outcome = await run(settings, sources, claude)
    assert "The lookup budget is spent." in "\n".join(claude.read)
    assert outcome.report["stop_reason"] == "budget_exhausted"


async def test_too_many_triage_turns_stop_the_run(settings, sources):
    claude = FakeClaude(
        [("Triage.", [call("read_evidence", evidence_id="ev-001")])] * 5
    )
    outcome = await run(settings, sources, claude)
    assert outcome.report["stop_reason"] == "no_triage"
    assert claude.interrupted


async def test_a_rejected_key_is_an_agent_error(settings, sources):
    claude = FakeClaude(
        [
            result(
                is_error=True,
                subtype="error_during_execution",
                api_error_status=401,
                result="invalid x-api-key",
            )
        ]
    )
    with pytest.raises(AgentError, match="invalid x-api-key"):
        await run(settings, sources, claude)


async def test_a_failed_model_call_still_gives_a_report(settings, sources):
    claude = FakeClaude(
        [
            ("Triage.", [triage(MISSING)]),
            result(
                is_error=True, subtype="error_during_execution", result="overloaded"
            ),
        ]
    )
    outcome = await run(settings, sources, claude)
    assert outcome.report["stop_reason"] == "model_error"
    assert len(outcome.report["gaps"]) == len(MISSING)


def test_inputs_default_to_opus_at_high_effort(monkeypatch):
    monkeypatch.setenv("BUG_ID", str(BUG))
    monkeypatch.setenv("MODEL", "")  # compose passes unset inputs as empty
    inputs = AgentInputs()
    assert (inputs.model, inputs.effort) == ("claude-opus-5-5", "high")
    assert not inputs.before_first_patch and inputs.as_of is None


async def test_bug_fix_gets_all_the_missing_information(
    settings, sources, tmp_path, monkeypatch
):
    """The entry point hands bug-fix every answer, at any confidence, and what
    stays unknown: nothing is filtered out."""
    import hackbot_agents.crash_info.__main__ as entry

    settings.investigation.max_tool_calls = 1
    claude = FakeClaude(
        [
            ("Triage.", [triage(MISSING)]),
            (
                "Read the function.",
                [call("read_code", gap_id="gap-001", file="dom/foo/Foo.cpp", line=42)],
            ),
            (
                "Answering.",
                [
                    call(
                        "record_finding",
                        gap_id="gap-001",
                        status="inferred",
                        answer="A medium-confidence guess about mWidget.",
                        evidence_ids=["ev-003"],
                        confidence="medium",
                        reasoning="The report says mWidget seems null.",
                    ),
                    *(
                        call("record_finding", gap_id=f"gap-00{n}", status="unknown")
                        for n in (2, 3, 4)
                    ),
                    call("finish"),
                ],
            ),
            ("Done.", []),
        ]
    )
    real = entry.run_crash_info

    async def run(bug_id, _settings, **_):
        return await real(
            bug_id,
            settings,
            sources=sources,
            client_factory=claude,
            progress=lambda m: None,
        )

    monkeypatch.setattr(entry, "run_crash_info", run)
    monkeypatch.setenv("BUG_ID", str(BUG))
    published: dict[str, str] = {}
    ctx = type(
        "Ctx",
        (),
        {
            "anthropic": type("Auth", (), {"api_key": "test"})(),
            "config": type("Config", (), {"source": None})(),
            "log_path": tmp_path / "agent.log",
            "run_id": "test",
            "publish_json": lambda self, key, payload: published.update({key: "json"}),
            "publish_file": lambda self, key, path, _type: published.update(
                {key: path.read_text()}
            ),
        },
    )()
    await entry.main(ctx)
    handoff = published[entry.HANDOFF_ARTIFACT]
    assert "A medium-confidence guess about mWidget." in handoff
    assert "Inferred (medium confidence)" in handoff
    assert "Still unknown:" in handoff


async def test_a_triage_setting_aside_many_labels_goes_back_once(
    settings, sources, monkeypatch
):
    monkeypatch.setattr(agent, "MAX_SET_ASIDE", SET_ASIDE)
    claude = FakeClaude(
        [
            ("Triage.", [triage(MISSING)]),  # 8 labels set aside
            ("Triage again, as it was.", [triage(MISSING)]),
            (
                "No answers to give.",
                [
                    *(
                        call("record_finding", gap_id=f"gap-00{n}", status="unknown")
                        for n in range(1, 5)
                    ),
                    call("finish"),
                ],
            ),
            # The toolbox's own gate: gap-001 was not looked up (twice at most).
            ("Finish.", [call("finish")]),
            ("Finish.", [call("finish")]),
            ("Done.", []),
        ]
    )
    outcome = await run(settings, sources, claude)
    read = "\n".join(claude.read)
    assert "Triage not accepted yet: you set aside 8 labels" in read
    assert read.count("Triage not accepted yet") == 1  # once only
    assert len(outcome.report["gaps"]) == len(MISSING)
    # The guidance for Claude is in its system prompt.
    assert "find which caller on the crashing stack" in claude.system


async def test_finish_waits_for_enough_lookups(settings, sources, monkeypatch):
    monkeypatch.setattr(agent, "MIN_LOOKUPS", LOOKUPS)  # half the budget of 6: 3
    answers = [
        call("record_finding", gap_id=f"gap-00{n}", status="unknown")
        for n in range(1, 5)
    ]
    look = call("read_code", gap_id="gap-001", file="dom/foo/Foo.cpp", line=42)
    claude = FakeClaude(
        [
            ("Triage.", [triage(MISSING)]),
            ("Answering.", [look, *answers, call("finish")]),
            ("Still nothing to add.", [call("finish")]),
            ("Finish anyway.", [call("finish")]),
            ("Done.", []),
        ]
    )
    outcome = await run(settings, sources, claude)
    read = "\n".join(claude.read)
    assert read.count("Not finished: you used 1 of your 6 lookups") == 2
    assert outcome.report["stop_reason"] == "finished"  # accepted the third time
