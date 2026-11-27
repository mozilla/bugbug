"""The agent's tools, organized by the taxonomy of crash missing information.

They are framework-neutral: the engine (the Claude Agent SDK loop in
``agent.py``) hands every tool call to :meth:`Toolbox.call` and returns its text
to the model.

- ``triage`` (first): the agent assesses every taxonomy label against the report:
  the information is missing (a gap, phrased as a question about this crash),
  already in the report, or not applicable.
- Lookups: each one serves one gap, and its description names the labels it
  usually serves (the taxonomy's ``actions``). They run through
  :class:`Recovery`: they see only what existed at the cutoff, results are
  stored as evidence with ids and provenance, and each one counts against the
  budget.
- ``add_gap``: missing information found on the way, with its label.
- ``record_finding``: the answer to one gap, checked against the evidence it cites.
- ``finish``: accepted once every gap has an answer and every high-relevance gap
  whose label is recoverable from evidence was investigated.
"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from .evidence import EvidenceStore, evidence_head, resolve_ids
from .models import (
    ActionRecord,
    ActionType,
    CrashInformationState,
    GapStatus,
    Inference,
    InformationGap,
    Level,
    ValidationRecord,
)
from .prompting import clip, one_line
from .recovery import ActionResult, Lookup, Recovery, stack_frames_in, usable_stack
from .schema import llm_schema
from .sources import Sources
from .taxonomy import Taxonomy
from .textutil import tokens

Phase = Literal["triage", "investigate", "report"]
Say = Callable[[str], None]

OBSERVATION_ITEM_CHARS = 6_000
READ_EVIDENCE_CHARS = 12_000
MAX_GAPS_PER_LABEL = 2
# How often a step goes back to the agent before it is taken as it is.
MAX_TRIAGE_REJECTIONS = 2
MAX_FINISH_REJECTIONS = 2
CRASH_STATS_COUNTS = (
    "Crash-stats aggregates for a signature over the days before the cutoff: counts "
    "by product, version, platform, process type, crash address and shutdown phase."
)
CRASH_STATS_WITH_STACK = (
    CRASH_STATS_COUNTS[:-1] + "; and the crashing thread's stack of the latest such "
    "crash with a usable one (when no crash has the signature exactly: of the "
    "crashes whose signature contains its most specific frames)."
)
CRASH_REPORT_WITH_STACK = (
    " When the report is gone or has no usable stack: the latest crash of the same "
    "signature before the cutoff that has one."
)
# Once, before finishing (settings' investigation.answer_review).
REVIEW = """Before finishing, review each answer below against the evidence it cites:
1. Does it answer its question, for this crash?
2. What exactly do the cited sources establish? Where they settle more than the \
answer says, revise it with record_finding, citing them: which case holds (for \
example which object a value is read from, or which caller reaches the crash), and \
which cases are affected and, when the evidence shows it, which similar ones are not \
(a configuration, an input method, a platform, a caller): "only when ...", rather \
than "with ... active".
3. Where the sources do not settle a point, keep it open: do not turn a guess into \
a fact.
Revise only the answers that need it, then call finish again.

The answers:
"""
# An "established" answer the evidence does not support goes back once; the next
# one is kept as an inference with low confidence.
MAX_FINDING_REJECTIONS = 1

ACTION_TOOLS = {
    ActionType.SEARCH_CODE: ("read_code", "search_code"),
    ActionType.INSPECT_REVISION: ("file_history", "read_changeset"),
    ActionType.INSPECT_CRASH_EVIDENCE: ("crash_stats", "crash_report"),
    ActionType.SEARCH_BUGZILLA: ("search_bugs", "read_bug"),
    ActionType.SEARCH_PRIOR_BUGS: ("prior_bugs",),
}


# --- Tool arguments ------------------------------------------------------------


class GapLookup(BaseModel):
    gap_id: str = Field(description="the gap this lookup serves, e.g. gap-002")


class ReadCode(GapLookup):
    file: str = Field(description="path in the tree, e.g. 'dom/base/nsINode.cpp'")
    line: int | None = Field(None, description="a line inside the code to read")
    symbol: str | None = Field(
        None, description="a function to read whole, e.g. 'nsINode::GetParent'"
    )


class SearchCode(GapLookup):
    query: str = Field(
        description="identifier or text to find, e.g. 'nsWindow::Destroy' or 'mWidget'"
    )
    path_prefix: str | None = Field(
        None, description="only files under this path, e.g. 'widget/windows/'"
    )


class FileHistory(GapLookup):
    file: str = Field(description="path in the tree")


class ReadChangeset(GapLookup):
    node: str = Field(description="changeset hash (12 or 40 hex digits)")


class CrashStats(GapLookup):
    signature: str = Field(description="crash signature, without the [@ ] brackets")


class CrashReport(GapLookup):
    crash_id: str = Field(description="crash report id (a UUID)")


class SearchBugs(GapLookup):
    query: str | None = Field(
        None, description="words that must all appear in a bug, e.g. a function name"
    )
    signature: str | None = Field(None, description="part of a crash signature")

    @model_validator(mode="after")
    def _one_criterion(self) -> "SearchBugs":
        if not (self.query or self.signature):
            raise ValueError("give a query or a signature")
        return self


class ReadBug(GapLookup):
    bug_id: int


class PriorBugsArgs(GapLookup):
    query: str | None = Field(
        None,
        description="what the earlier bugs should be about, e.g. a function, an object "
        "or a mechanism ('nsWindow::Destroy', 'editing host null'); empty: like this crash",
    )


class ReadEvidence(BaseModel):
    evidence_id: str = Field(description="an evidence id such as ev-012")


class LabelAssessment(BaseModel):
    label: str = Field(description="taxonomy label id, e.g. Obj.LIFETIME")
    status: Literal["missing", "in_report", "not_applicable"]
    question: str = Field(
        "",
        description="missing: the information a developer fixing this crash needs, "
        "as one question naming this crash's code, values or conditions",
    )
    why_it_matters: str = Field(
        "", description="missing: why the fix depends on it, in one short sentence"
    )
    relevance: Literal["high", "medium", "low"] = "medium"
    reason: str = Field(
        "",
        description="in_report: what the report states; not_applicable: why it does "
        "not matter for this crash",
    )
    evidence_ids: list[str] = Field(
        [], description="in_report: the report items that state it"
    )
    quote: str = Field(
        "",
        description="in_report: the sentence of the report that states the answer, "
        "copied exactly from a cited item",
    )


class Triage(BaseModel):
    labels: list[LabelAssessment] = Field(
        description="every taxonomy label, at least once"
    )


class AddGap(BaseModel):
    label: str = Field(description="taxonomy label id")
    question: str = Field(description="the missing information, as a question")
    why_it_matters: str = ""
    relevance: Literal["high", "medium", "low"] = "medium"


class RecordFinding(BaseModel):
    gap_id: str
    status: Literal["established", "inferred", "unknown", "not_applicable"]
    answer: str = Field(
        "",
        description="established or inferred: the missing information itself, "
        "answering the label's canonical question for this crash, in at most two "
        "sentences; not_applicable: why",
    )
    evidence_ids: list[str] = Field([], description="the evidence ids it rests on")
    confidence: Literal["high", "medium", "low"] = "low"
    reasoning: str = Field(
        "",
        description="inferred: how the evidence leads there, citing ids, in one "
        "or two sentences",
    )
    alternatives: list[str] = Field(
        [], description="inferred: at most one other plausible answer, in one sentence"
    )
    how_to_confirm: str = Field(
        "",
        description="inferred: the check that would confirm or refute it, in one "
        "sentence",
    )


class Finish(BaseModel):
    summary: str = Field(
        "", description="one or two sentences: what the investigation established"
    )


# --- Tools -----------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    args: type[BaseModel]
    description: str
    # The lookup the tool runs and its Recovery arguments; None for the tools that
    # call no source.
    action: ActionType | None = None
    arguments: Callable[[Any], dict[str, Any]] | None = None


LOOKUPS = (
    Tool(
        "read_code",
        ReadCode,
        "Read source code at the pinned revision: the whole function that contains "
        "`line` or defines `symbol`, else the lines around `line`.",
        ActionType.SEARCH_CODE,
        lambda a: {"file": a.file, "line": a.line, "query": a.symbol},
    ),
    Tool(
        "search_code",
        SearchCode,
        "Search the source tree at the pinned revision for an identifier or text; "
        "returns the matching lines and the code around the best match.",
        ActionType.SEARCH_CODE,
        lambda a: {"query": a.query, "path_prefix": a.path_prefix},
    ),
    Tool(
        "file_history",
        FileHistory,
        "List the latest changesets that touched a file, up to the pinned revision.",
        ActionType.INSPECT_REVISION,
        lambda a: {"file": a.file},
    ),
    Tool(
        "read_changeset",
        ReadChangeset,
        "Read a changeset's description and diff (refused for changesets pushed "
        "after the cutoff).",
        ActionType.INSPECT_REVISION,
        lambda a: {"node": a.node},
    ),
    Tool(
        "crash_stats",
        CrashStats,
        CRASH_STATS_WITH_STACK,
        ActionType.INSPECT_CRASH_EVIDENCE,
        lambda a: {"signature": a.signature},
    ),
    Tool(
        "crash_report",
        CrashReport,
        "Read one processed crash report: reason, address, annotations and the "
        "crashing thread's stack.",
        ActionType.INSPECT_CRASH_EVIDENCE,
        lambda a: {"crash_id": a.crash_id},
    ),
    Tool(
        "search_bugs",
        SearchBugs,
        "Search Bugzilla for other bugs, as they were at the cutoff, by words or by "
        "crash signature.",
        ActionType.SEARCH_BUGZILLA,
        lambda a: {"signature": a.signature} if a.signature else {"query": a.query},
    ),
    Tool(
        "read_bug",
        ReadBug,
        "Read another bug as it was at the cutoff: fields, signatures and first comments.",
        ActionType.SEARCH_BUGZILLA,
        lambda a: {"bug_id": a.bug_id},
    ),
    Tool(
        "prior_bugs",
        PriorBugsArgs,
        "Earlier crash bugs fixed before the cutoff that resemble this one (or match "
        "`query`): their report, the discussion that led to the fix, and the fix "
        "itself. They show what was missing in similar crashes and what the repair had "
        "to do; they support inferences about this bug, not established facts.",
        ActionType.SEARCH_PRIOR_BUGS,
        lambda a: {"query": a.query},
    ),
)
OTHER_TOOLS = (
    Tool(
        "triage",
        Triage,
        "Assess every taxonomy label against the bug report: missing (a gap to "
        "investigate), in_report, or not_applicable. Your first call.",
    ),
    Tool(
        "add_gap",
        AddGap,
        "Add missing information found during the investigation, with its taxonomy label.",
    ),
    Tool(
        "record_finding",
        RecordFinding,
        "Record the answer to one gap: established (the cited evidence states it), "
        "inferred, unknown, or not_applicable. A later call replaces it.",
    ),
    Tool(
        "read_evidence",
        ReadEvidence,
        "Show an evidence item again, in full (uses no lookup).",
    ),
    Tool(
        "finish",
        Finish,
        "End the investigation, once every gap has a recorded answer.",
    ),
)
PHASE_TOOLS: dict[str, tuple[str, ...]] = {
    "triage": ("triage", "read_evidence"),
    "investigate": (
        *(t.name for t in LOOKUPS),
        "add_gap",
        "record_finding",
        "read_evidence",
        "finish",
    ),
    "report": ("record_finding", "read_evidence", "finish"),
}
FINISHED = "Not run: the investigation is finished."
UNAVAILABLE = {
    "triage": "Not available yet: start with triage, assessing every taxonomy label.",
    "investigate": "Triage is done: add missing information with add_gap.",
    "report": "Not available now: the lookups are over. Record your answers "
    "(record_finding), then call finish.",
}


@dataclass
class ToolOutcome:
    """What the tool calls of one model turn did to the investigation."""

    triaged: bool = False
    finished: bool = False


class Toolbox:
    def __init__(
        self,
        state: CrashInformationState,
        store: EvidenceStore,
        sources: Sources,
        taxonomy: Taxonomy,
        say: Say,
        max_gaps: int = 12,
        crash_data: bool = True,
        answer_review: bool = True,
    ) -> None:
        self.state = state
        self.store = store
        self.taxonomy = taxonomy
        self.say = say
        self.max_gaps = max_gaps
        self.crash_data = crash_data
        self.answer_review = answer_review
        self.reviewed = False
        self.recovery = Recovery(state, store, sources, crash_data=crash_data)
        lookups = [
            _crash_data_described(t, crash_data)
            for t in (LOOKUPS if state.mode.investigation else ())
            if state.mode.knowledge or t.action != ActionType.SEARCH_PRIOR_BUGS
        ]
        self.tools = {t.name: t for t in (*lookups, *OTHER_TOOLS)}
        # Lookups already run, by identity, with what they gave.
        self.done: dict[str, str] = {}
        # Gaps with a recorded answer; rejections per step ("triage", "finish", gap id).
        self.recorded: set[str] = set()
        self.rejections: Counter[str] = Counter()

    @property
    def lookups_left(self) -> int:
        return self.state.investigation_budget.tool_calls_left

    def describe(self, tool: Tool) -> str:
        """The tool's description, with the labels a lookup usually serves."""
        if tool.action is None:
            return tool.description
        served = [
            label.id for label in self.taxonomy.labels if tool.action in label.actions
        ]
        return f"{tool.description} Usually serves: {', '.join(served)}."

    def definitions(self, phase: Phase) -> list[dict[str, Any]]:
        """The tools offered in ``phase``, as function definitions."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": self.describe(tool),
                    "parameters": llm_schema(tool.args),
                },
            }
            for name in PHASE_TOOLS[phase]
            if (tool := self.tools.get(name)) is not None
        ]

    async def call(
        self,
        name: str,
        raw: dict[str, Any],
        thought: str,
        phase: Phase,
        outcome: ToolOutcome,
    ) -> str:
        """Run one tool call of a model turn (``outcome`` gathers what the turn's
        calls did) and return the observation; ``thought`` is the reasoning the
        model gave for the turn, kept with the lookups it runs."""
        if outcome.finished:
            return FINISHED
        tool = self.tools.get(name)
        if tool is None:
            return f"There is no tool {name!r}."
        if name not in PHASE_TOOLS[phase] or (name == "triage" and outcome.triaged):
            return UNAVAILABLE[phase]
        try:
            args = tool.args.model_validate(raw)
        except ValidationError as e:
            return f"Invalid arguments for {name}: {_errors(e)}"
        if name == "triage":
            reply, outcome.triaged = self._triage(args)
            return reply
        if name == "add_gap":
            return self._add_gap(args)
        if name == "record_finding":
            return self._record(args)
        if name == "read_evidence":
            return self._read_evidence(args.evidence_id)
        if name == "finish":
            reply, outcome.finished = self._finish(args, phase)
            return reply
        return await self._lookup_for_gap(tool, args, thought)

    # --- Triage and gaps ---------------------------------------------------------

    def _triage(self, args: Triage) -> tuple[str, bool]:
        problems = self._check_triage(args)
        if problems and self.rejections["triage"] < MAX_TRIAGE_REJECTIONS:
            self.rejections["triage"] += 1
            self.say(f"[tools] triage sent back: {'; '.join(problems)[:300]}")
            return (
                "Triage not accepted:\n"
                + "\n".join(f"- {p}" for p in problems)
                + "\nCall triage again with every label.",
                False,
            )
        self._apply_triage(args)
        gaps = self.open_gaps()
        set_aside = len(self.state.information_gaps) - len(gaps)
        self.say(f"[tools] triage: {len(gaps)} gaps, {set_aside} labels not missing")
        for gap in gaps:
            self.say(
                f"      {gap.id} [{gap.taxonomy_label}] ({gap.relevance.value}) {gap.question}"
            )
        return (
            "Triage recorded.\n"
            + self.board()
            + "\nInvestigate the gaps, most relevant first; record each answer with "
            "record_finding.",
            True,
        )

    def _check_triage(self, args: Triage) -> list[str]:
        problems = []
        known = self._evidence_ids()
        anchors = crash_anchors(self.state)
        assessed: set[str] = set()
        missing = Counter[str]()
        for n, item in enumerate(args.labels, 1):
            label = self.taxonomy.normalize(item.label)
            if label is None:
                problems.append(f"item {n}: unknown label {item.label!r}")
                continue
            assessed.add(label)
            if item.status == "missing":
                missing[label] += 1
                if not is_specific(item.question, anchors):
                    problems.append(
                        f"item {n} ({label}): the question names nothing specific to "
                        "this crash; name its code, values or conditions"
                    )
            elif item.status == "in_report":
                problems += [
                    f"item {n} ({label}): {problem}"
                    for problem in self._in_report_problems(label, item, known)
                ]
            elif item.status == "not_applicable" and not item.reason.strip():
                problems.append(f"item {n} ({label}): say why it does not apply")
        unassessed = [i for i in self.taxonomy.ids if i not in assessed]
        if unassessed:
            problems.append(f"labels not assessed: {', '.join(unassessed)}")
        crowded = [
            label for label, count in missing.items() if count > MAX_GAPS_PER_LABEL
        ]
        if crowded:
            problems.append(
                f"at most {MAX_GAPS_PER_LABEL} missing pieces per label: {', '.join(crowded)}"
            )
        if sum(missing.values()) > self.max_gaps:
            problems.append(f"at most {self.max_gaps} missing pieces in all")
        return problems

    def _in_report_problems(
        self, label: str, item: LabelAssessment, known: set[str]
    ) -> list[str]:
        """Setting a label aside as already in the report takes the report's own
        sentence: raw data (a stack, registers, an assertion) states no answer, and
        a label set aside is never looked at again."""
        cited = resolve_ids(item.evidence_ids, known)
        if not cited:
            return ["cite the report items that state it, or mark it missing"]
        problems = []
        texts = [e.content for i in cited if (e := self.store.get(i))]
        if not _quoted(item.quote, texts):
            problems.append(
                "quote the sentence of the report that states the answer, copied "
                "exactly from a cited item (raw data such as a stack, registers or an "
                "assertion message is not an answer), or mark it missing"
            )
        if label == "Verif.EXPECTED" and _TRIVIAL_EXPECTATION.search(
            f"{item.reason} {item.quote}"
        ):
            problems.append(
                "not crashing, or the asserted condition, is not the expected behavior: "
                "it is what the code must do instead in that situation; mark it missing "
                "unless the report states that"
            )
        return problems

    def _apply_triage(self, args: Triage) -> None:
        state, known = self.state, self._evidence_ids()
        items = [
            (label, item)
            for item in args.labels
            if (label := self.taxonomy.normalize(item.label))
        ]
        # Gaps first, so that they get the first ids; then the labels set aside.
        per_label = Counter[str]()
        for label, item in items:
            if item.status != "missing" or not item.question.strip():
                continue
            if (
                per_label[label] == MAX_GAPS_PER_LABEL
                or len(self.open_gaps()) == self.max_gaps
            ):
                continue
            per_label[label] += 1
            relevance = Level(item.relevance)
            gap = InformationGap(
                id=state.next_id("gap"),
                taxonomy_label=label,
                question=item.question.strip(),
                why_it_matters=item.why_it_matters.strip(),
                relevance=relevance,
            )
            state.information_gaps.append(gap)
            gap.set_status(
                GapStatus.CONFIRMED_MISSING,
                "missing from the report (triage)",
                state.step,
            )
        set_aside: set[str] = set()
        for label, item in items:
            if item.status == "missing" or label in per_label or label in set_aside:
                continue
            set_aside.add(label)
            what = (
                "already in the report"
                if item.status == "in_report"
                else "not applicable"
            )
            self._set_aside(
                label,
                f"{what}: {item.reason.strip() or 'no reason given'}",
                item.question.strip(),
                resolve_ids(item.evidence_ids, known),
            )
        for label in self.taxonomy.ids:
            if label not in per_label and label not in set_aside:
                self._set_aside(label, "not assessed by the agent")

    def _set_aside(
        self,
        label: str,
        reason: str,
        question: str = "",
        evidence_ids: list[str] | None = None,
    ) -> None:
        """Record a label the triage found not missing, with the reason."""
        gap = InformationGap(
            id=self.state.next_id("gap"),
            taxonomy_label=label,
            question=question or self.taxonomy.label(label).canonical_question,
            relevance=Level.LOW,
            evidence_ids=evidence_ids or [],
        )
        self.state.information_gaps.append(gap)
        gap.set_status(GapStatus.NOT_APPLICABLE, reason, self.state.step)

    def _add_gap(self, args: AddGap) -> str:
        label = self.taxonomy.normalize(args.label)
        if label is None:
            return f"Unknown label {args.label!r}; use one of {', '.join(self.taxonomy.ids)}."
        if not args.question.strip():
            return "A gap needs its question."
        if len(self.open_gaps()) >= self.max_gaps:
            return f"There are already {self.max_gaps} gaps: no more can be added."
        relevance = Level(args.relevance)
        late = self.taxonomy.label(label).phase == "late"
        gap = InformationGap(
            id=self.state.next_id("gap"),
            taxonomy_label=label,
            question=args.question.strip(),
            why_it_matters=args.why_it_matters.strip(),
            relevance=relevance,
            origin="late_discovery" if late else "evidence_followup",
        )
        self.state.information_gaps.append(gap)
        gap.set_status(
            GapStatus.CONFIRMED_MISSING,
            "found during the investigation",
            self.state.step,
        )
        self.say(f"[tools] new gap {gap.id} [{label}] {gap.question}")
        return f"Added {gap.id} [{label}].\n" + self.board()

    def open_gaps(self) -> list[InformationGap]:
        """The gaps of the report: every gap not set aside."""
        return [
            g
            for g in self.state.information_gaps
            if g.status != GapStatus.NOT_APPLICABLE
        ]

    def _gap(self, raw: str) -> InformationGap | None:
        number = re.search(r"\d+", str(raw))
        return self.state.gap(f"gap-{int(number.group()):03d}") if number else None

    def board(self) -> str:
        """The agent's working view of the gaps, by taxonomy label."""
        lines = ["Gaps:"]
        for gap in self.open_gaps():
            if gap.status == GapStatus.RECOVERED:
                answer = "established"
            elif gap.inference:
                answer = f"inferred ({gap.inference.confidence.value})"
            elif gap.id in self.recorded:
                answer = "unknown"
            else:
                answer = "no answer recorded"
            lines.append(
                f"- {gap.id} [{gap.taxonomy_label}] {gap.relevance.value}, "
                f"{gap.automatic_attempts} lookup(s), {answer}: {one_line(gap.question, 140)}"
            )
        set_aside = [
            f"{g.taxonomy_label} ({g.status_history[-1].reason.split(':')[0]})"
            for g in self.state.information_gaps
            if g.status == GapStatus.NOT_APPLICABLE
        ]
        if set_aside:
            lines.append("Not missing: " + "; ".join(set_aside))
        if self.state.mode.investigation:
            lines.append(f"Lookups left: {self.lookups_left}")
        return "\n".join(lines)

    def remaining(self) -> str:
        """After a finding, the gaps still without an answer: the whole board came
        with triage, and repeating it after every finding only costs tokens."""
        left = [
            f"{g.id} [{g.taxonomy_label}]"
            for g in self.open_gaps()
            if g.id not in self.recorded
        ]
        lines = ["Still without an answer: " + (", ".join(left) or "none")]
        if self.state.mode.investigation:
            lines.append(f"Lookups left: {self.lookups_left}")
        return "\n".join(lines)

    # --- Knowledge ---------------------------------------------------------------------

    async def recall(self, limit: int) -> list[str]:
        """Earlier bugs fixed before the cutoff that resemble this one, gathered
        before triage (outside the lookup budget); returns their evidence ids."""
        state = self.state
        state.step += 1
        lookup = Lookup(ActionType.SEARCH_PRIOR_BUGS, {"limit": limit})
        record = ActionRecord(
            id=state.next_id("act"),
            round=state.round,
            step=state.step,
            action=lookup.action,
            arguments={"tool": "recall", **lookup.arguments},
            rationale="recall earlier bugs fixed before the cutoff, before triage",
            decided_by="fallback",
        )
        state.actions_taken.append(record)
        result = await self.recovery.run(lookup, record.id)
        record.outcome = result.outcome
        record.evidence_ids = result.evidence_ids
        record.error = result.note
        record.duration_s = round(result.duration_s, 2)
        self.say(
            f"[recall] {len(result.evidence_ids)} earlier bug(s) fixed before the cutoff"
            + (f" ({result.note})" if result.note else "")
        )
        for evidence_id in result.evidence_ids:
            self.say(f"      {evidence_id} {self.store.get(evidence_id).summary}")
        return result.evidence_ids

    async def complete_stack(self, limit: int = 2) -> list[str]:
        """A report without a stack: the crash data it lacks, gathered before
        triage outside the lookup budget: the crash reports it links to, then its
        signatures (their counts and the stack of their latest crash), until a
        usable stack is found; returns the evidence ids."""
        state = self.state
        snap = state.report_snapshot
        if not self.crash_data or state.indicators.stack_frames or snap is None:
            return []
        lookups = [
            ("crash_report", {"crash_id": c})
            for c in state.indicators.crash_ids[:limit]
        ]
        lookups += [
            ("crash_stats", {"signature": s}) for s in snap.crash_signatures[:limit]
        ]
        found: list[str] = []
        for tool, arguments in lookups:
            state.step += 1
            lookup = Lookup(ActionType.INSPECT_CRASH_EVIDENCE, arguments)
            record = ActionRecord(
                id=state.next_id("act"),
                round=state.round,
                step=state.step,
                action=lookup.action,
                arguments={"tool": tool, **arguments},
                rationale="the report holds no stack",
                decided_by="fallback",
            )
            state.actions_taken.append(record)
            result = await self.recovery.run(lookup, record.id)
            record.outcome = result.outcome
            record.evidence_ids = result.evidence_ids
            record.error = result.note
            record.duration_s = round(result.duration_s, 2)
            target = arguments.get("crash_id") or f"[@ {arguments.get('signature')}]"
            self.say(
                f"[crash_data] {tool} {target}: {len(result.evidence_ids)} item(s)"
                + (f" ({result.note})" if result.note else "")
            )
            for evidence_id in result.evidence_ids:
                self.say(f"      {evidence_id} {self.store.get(evidence_id).summary}")
            found += result.evidence_ids
            if any(
                usable_stack(stack_frames_in(self.store.get(i).content)) for i in found
            ):
                break
        return found

    def is_prior_bug(self, evidence_id: str) -> bool:
        evidence = self.store.get(evidence_id)
        return evidence is not None and evidence.kind == "prior_bug"

    # --- Lookups ---------------------------------------------------------------------

    async def _lookup_for_gap(self, tool: Tool, args: Any, thought: str) -> str:
        assert tool.action is not None and tool.arguments is not None
        gap = self._gap(args.gap_id)
        if gap is None or gap.status == GapStatus.NOT_APPLICABLE:
            return (
                f"{args.gap_id!r} is not an open gap; use one of the gap ids, or add "
                "the missing information with add_gap first.\n" + self.board()
            )
        if tool.name == "read_bug" and args.bug_id == self.state.bug_id:
            return (
                "That is the bug under investigation: its report is in the task above."
            )
        arguments = {
            k: v for k, v in tool.arguments(args).items() if v not in (None, "")
        }
        lookup = Lookup(tool.action, arguments)
        if lookup.key in self.done:
            return f"You already ran this lookup: {self.done[lookup.key]}"
        if self.lookups_left <= 0:
            return "Refused: the lookup budget is spent."
        state, budget = self.state, self.state.investigation_budget
        state.step += 1
        record = ActionRecord(
            id=state.next_id("act"),
            round=state.round,
            step=state.step,
            action=lookup.action,
            gap_id=gap.id,
            arguments={"tool": tool.name, **arguments},
            rationale=thought,
            decided_by="llm",
        )
        state.actions_taken.append(record)
        budget.tool_calls_used += 1
        gap.automatic_attempts += 1
        gap.investigation_history.append(record.id)
        if gap.status == GapStatus.CONFIRMED_MISSING:
            gap.set_status(
                GapStatus.INVESTIGATING, f"investigating with {tool.name}", state.step
            )
        result = await self.recovery.run(lookup, record.id)
        record.outcome = result.outcome
        record.evidence_ids = result.evidence_ids
        record.error = result.note
        record.duration_s = round(result.duration_s, 2)
        shown = " ".join(f"{k}={v}" for k, v in arguments.items())
        self.say(
            f"[tools] step {state.step}: {tool.name} {shown} for {gap.id} -> "
            f"{result.outcome.replace('_', ' ')} {' '.join(result.evidence_ids)}"
            + (f" ({result.note})" if result.note else "")
        )
        self.done[lookup.key] = _gave(result)
        footer = (
            f"\n({gap.id} [{gap.taxonomy_label}]: {gap.automatic_attempts} lookup(s); "
            f"lookups left: {self.lookups_left})"
        )
        waiting = self.uninvestigated()
        if waiting:
            footer += (
                "\nNot investigated yet (high relevance, recoverable from evidence): "
                + ", ".join(f"{g.id} [{g.taxonomy_label}]" for g in waiting)
            )
        return self._observation(result) + footer

    def uninvestigated(self) -> list[InformationGap]:
        """High-relevance gaps whose label is usually recoverable from evidence,
        still without a lookup or an established answer: where the taxonomy says
        the remaining lookups should go."""
        return [
            gap
            for gap in self.open_gaps()
            if gap.relevance == Level.HIGH
            and self.taxonomy.label(gap.taxonomy_label).recoverable_by != "developer"
            and gap.status != GapStatus.RECOVERED
            and gap.automatic_attempts == 0
        ]

    def _observation(self, result: ActionResult) -> str:
        if result.outcome == "blocked":
            return f"Refused: {result.note}. Information from after the cutoff is withheld."
        if result.outcome == "error":
            return f"The lookup failed: {result.note}"
        blocks = [self.item(i, OBSERVATION_ITEM_CHARS) for i in result.evidence_ids]
        if result.seen_ids:
            blocks.append(
                "Already gathered: "
                + "; ".join(
                    f"[{i}] {self.store.get(i).summary}" for i in result.seen_ids
                )
            )
        if not blocks:
            note = result.note if result.note not in (None, "no results") else None
            return f"No results ({note})." if note else "No results."
        return "\n\n".join(blocks)

    def item(self, evidence_id: str, limit: int) -> str:
        e = self.store.get(evidence_id)
        assert e is not None
        return f"{evidence_head(e)}\n{clip(e.content, limit)}"

    def _read_evidence(self, raw_id: str) -> str:
        found = resolve_ids([raw_id], self._evidence_ids())
        if not found:
            return f"There is no evidence item {raw_id!r}."
        return self.item(found[0], READ_EVIDENCE_CHARS)

    def _evidence_ids(self) -> set[str]:
        return {e.id for e in self.state.evidence}

    # --- Findings ----------------------------------------------------------------------

    def _record(self, args: RecordFinding) -> str:
        state = self.state
        gap = self._gap(args.gap_id)
        # A gap the agent itself recorded as not applicable may be recorded again.
        if gap is None or (
            gap.status == GapStatus.NOT_APPLICABLE and gap.id not in self.recorded
        ):
            return f"{args.gap_id!r} is not an open gap.\n" + self.board()
        known = self._evidence_ids()
        cited = resolve_ids(args.evidence_ids, known)
        unknown_ids = [i for i in args.evidence_ids if not resolve_ids([i], known)]
        answer = args.answer.strip()
        if unknown_ids:
            return (
                f"No evidence has the id(s) {', '.join(unknown_ids)}; nothing recorded."
            )
        if args.status != "unknown" and not answer:
            return f"A finding recorded as {args.status} needs its answer; nothing recorded."
        if args.status == "inferred" and not state.mode.inference:
            return "Inference is off in this run: record established or unknown."
        invented = invented_identifiers(state, answer) if answer else []
        own = [i for i in cited if not self.is_prior_bug(i)]
        status, note = args.status, ""
        if status == "established" and (not own or invented):
            if not cited:
                problem = "cites no evidence"
            elif not own:
                problem = (
                    "rests only on earlier bugs, which support an inference about "
                    "this bug, not an established fact"
                )
            else:
                problem = f"names {', '.join(invented[:4])}, found in no evidence"
            if self.rejections[gap.id] < MAX_FINDING_REJECTIONS:
                self.rejections[gap.id] += 1
                return (
                    "Not recorded: an established answer must rest on evidence that "
                    f"states it, and this one {problem}. Cite that evidence, or record "
                    "the answer as inferred."
                )
            status = "inferred" if state.mode.inference else "unknown"
            note = f" It {problem}, so it is kept as {status}, not established."
        gap.finding, gap.finding_evidence_ids, gap.confidence = None, [], None
        gap.inference = None
        gap.evidence_ids = list(dict.fromkeys([*gap.evidence_ids, *cited]))
        if status == "established":
            gap.finding = answer
            gap.finding_evidence_ids = cited
            gap.confidence = Level(args.confidence)
            gap.set_status(
                GapStatus.RECOVERED, "established from the cited evidence", state.step
            )
            support = "supported"
        elif status == "inferred":
            grounded = bool(cited) and not invented
            gap.inference = Inference(
                answer=answer,
                reasoning=args.reasoning.strip(),
                evidence_ids=cited,
                confidence=Level(args.confidence) if grounded else Level.LOW,
                alternatives=args.alternatives[:1],
                how_to_confirm=args.how_to_confirm.strip(),
                grounded=grounded,
                round=state.round,
            )
            gap.set_status(
                GapStatus.UNRESOLVED, "inferred, not established", state.step
            )
            support = "supported" if grounded else ("weak" if cited else "unsupported")
            if not grounded and not note:
                problem = (
                    "cites no evidence"
                    if not cited
                    else f"names {', '.join(invented[:4])}, found in no evidence"
                )
                note = (
                    f" Its confidence is capped at low: it {problem}. Cite the "
                    "evidence it rests on and name only code that appears in it."
                )
        elif status == "unknown":
            gap.set_status(
                GapStatus.UNRESOLVED, "no answer from the evidence", state.step
            )
            support = "n/a"
        else:
            gap.set_status(
                GapStatus.NOT_APPLICABLE, f"not applicable: {answer}", state.step
            )
            support = "n/a"
        self.recorded.add(gap.id)
        state.validations.append(
            ValidationRecord(
                gap_id=gap.id,
                step=state.step,
                model_verdict=args.status,
                final_verdict=status,
                cited=cited,
                support=support,
                note=f"names found in no evidence: {', '.join(invented)}"
                if invented
                else "",
            )
        )
        self.say(f"[tools] {gap.id} [{gap.taxonomy_label}] {describe_gap(gap)}")
        return (
            f"Recorded {gap.id} [{gap.taxonomy_label}] as {status}.{note}\n"
            + self.remaining()
        )

    def _finish(self, args: Finish, phase: Phase) -> tuple[str, bool]:
        problems = []
        if phase != "report":
            problems = [
                f"{gap.id} [{gap.taxonomy_label}] has no recorded answer"
                for gap in self.open_gaps()
                if gap.id not in self.recorded
            ]
            if self.state.mode.investigation and self.lookups_left > 0:
                problems += [
                    f"{gap.id} [{gap.taxonomy_label}] is high-relevance and its label "
                    "is recoverable from evidence, but no lookup served it"
                    for gap in self.uninvestigated()
                ]
        if problems and self.rejections["finish"] < MAX_FINISH_REJECTIONS:
            self.rejections["finish"] += 1
            self.say(f"[tools] finish refused: {'; '.join(problems)[:300]}")
            return (
                "Not finished:\n"
                + "\n".join(f"- {p}" for p in problems)
                + "\n"
                + self.board(),
                False,
            )
        if self.answer_review and not self.reviewed and self.state.relevant_gaps():
            self.reviewed = True
            self.say("[tools] finish: the answers are reviewed first")
            return REVIEW + self.answers(), False
        if args.summary.strip():
            self.say(f"[tools] finish: {one_line(args.summary, 300)}")
        return "Investigation finished.", True

    def answers(self) -> str:
        """Each relevant gap's question and recorded answer, with its evidence."""
        lines = []
        for gap in self.state.relevant_gaps():
            if gap.finding:
                answer, ids = f"established: {gap.finding}", gap.finding_evidence_ids
            elif gap.inference:
                inf = gap.inference
                answer = f"inferred ({inf.confidence.value}): {inf.answer}"
                if inf.reasoning:
                    answer += f" Reasoning: {inf.reasoning}"
                ids = inf.evidence_ids
            else:
                answer, ids = f"{gap.status.value}: no answer", []
            cited = f" [{', '.join(ids)}]" if ids else ""
            lines.append(
                f"- {gap.id} [{gap.taxonomy_label}] {gap.question}\n"
                f"  {one_line(answer, 700)}{cited}"
            )
        return "\n".join(lines)


def _crash_data_described(tool: Tool, crash_data: bool) -> Tool:
    """The crash tools say what they return with crash data completion on, and
    only what they returned before without it."""
    if tool.name == "crash_stats":
        text = CRASH_STATS_WITH_STACK if crash_data else CRASH_STATS_COUNTS
        return replace(tool, description=text)
    if tool.name == "crash_report" and crash_data:
        return replace(tool, description=tool.description + CRASH_REPORT_WITH_STACK)
    return tool


# "It must not crash" restates the bug; it is never the expected behavior.
_TRIVIAL_EXPECTATION = re.compile(
    r"\b(not|never|without|no longer)\s+(crash|abort|assert|trap|fail)", re.IGNORECASE
)
# Words that appear in any crash discussion and so do not tie a question to this one.
_GENERIC_WORDS = frozenset(
    "null pointer function object code report value state thread process memory "
    "error failure issue firefox mozilla gecko browser repair crash crashes".split()
)
_IDENT = re.compile(
    r"\b(?:[A-Za-z_]\w*::)+~?[A-Za-z_]\w*\b|\b[a-z]+[A-Z]\w*\b|\bm[A-Z]\w+\b"
)


def crash_anchors(state: CrashInformationState) -> set[str]:
    """Terms that tie a question to this crash: signature, frames, identifiers,
    files, crash reasons, summary, product and component."""
    snap = state.report_snapshot
    ind = state.indicators
    texts = [
        *ind.identifiers,
        *ind.crash_reasons,
        *(f.function for f in ind.stack_frames),
    ]
    if snap is not None:
        texts += [
            snap.summary,
            snap.product or "",
            snap.component or "",
            *snap.crash_signatures,
        ]
    words: set[str] = set()
    for text in texts:
        for token in tokens(text):
            parts = [p for p in token.split("::") if p]
            # "mozilla::foo::bar" also anchors "foo::bar": how code and people
            # usually write it.
            words.update("::".join(parts[i:]) for i in range(len(parts) - 1))
            words.update(p for p in parts if len(p) >= 4)
    words.update(PurePosixPath(f).stem.lower() for f in ind.files)
    return {w for w in words if w not in _GENERIC_WORDS}


def is_specific(question: str, anchors: set[str]) -> bool:
    words = set()
    for token in tokens(question):
        words.add(token)
        words.update(part for part in token.split("::") if part)
    return bool(words & anchors)


def invented_identifiers(state: CrashInformationState, text: str) -> list[str]:
    """Code identifiers named in ``text`` that appear in no evidence: the typical
    signature of an invented detail."""
    everything = "\n".join(e.content for e in state.evidence)
    return sorted(name for name in set(_IDENT.findall(text)) if name not in everything)


def _quoted(quote: str, texts: list[str]) -> bool:
    """``quote`` (three words or more) appears in one of ``texts``, give or take
    case, spacing and surrounding quotation marks."""

    def norm(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()

    wanted = norm(quote).strip("\"'“”‘’ ")
    if len(re.findall(r"[a-z]{3,}", wanted)) < 3:
        return False
    return any(wanted in norm(text) for text in texts)


def _errors(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
        for e in error.errors()[:5]
    )


def _gave(result: ActionResult) -> str:
    ids = result.evidence_ids or result.seen_ids
    if ids:
        return "it gave " + ", ".join(ids) + "."
    return f"it gave nothing ({result.outcome.replace('_', ' ')}: {result.note})."


def describe_gap(gap: InformationGap) -> str:
    """One line for progress output."""
    if gap.status == GapStatus.RECOVERED:
        return f"established: {one_line(gap.finding or '', 220)}"
    if gap.inference:
        return f"inferred ({gap.inference.confidence.value}): {one_line(gap.inference.answer, 220)}"
    if gap.status == GapStatus.NOT_APPLICABLE:
        return gap.status_history[-1].reason if gap.status_history else "not applicable"
    return "no answer"
