"""The information state of one CrashInfo investigation.

The agent's tools read and update a single :class:`CrashInformationState`: the
report snapshot, the evidence with its provenance, the gaps with their answers,
and every lookup. It is saved with each report (``state.json``).
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

# 2.0: the staged pipeline and its developer questions were removed.
SCHEMA_VERSION = "2.0"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Level(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class GapStatus(StrEnum):
    """Lifecycle of an information gap."""

    # Missing from the report (triage), then looked up.
    CONFIRMED_MISSING = "confirmed_missing"
    INVESTIGATING = "investigating"
    # Established from cited evidence.
    RECOVERED = "recovered"
    # Inferred, unknown, or left without an answer.
    UNRESOLVED = "unresolved"
    # A label the triage found not missing, or a gap that turned out not to matter.
    NOT_APPLICABLE = "not_applicable"


class ActionType(StrEnum):
    """The kinds of lookup, each run by one handler of ``Recovery``."""

    SEARCH_BUGZILLA = "SEARCH_BUGZILLA"
    SEARCH_CODE = "SEARCH_CODE"
    INSPECT_REVISION = "INSPECT_REVISION"
    INSPECT_CRASH_EVIDENCE = "INSPECT_CRASH_EVIDENCE"
    # Earlier bugs fixed before the cutoff, with their fixes (knowledge.py).
    SEARCH_PRIOR_BUGS = "SEARCH_PRIOR_BUGS"


class EvidenceSource(StrEnum):
    BUGZILLA = "bugzilla"
    CRASH_STATS = "crash_stats"
    SEARCHFOX = "searchfox"
    HGMO = "hgmo"
    LOCAL_REPO = "local_repo"


class Provenance(BaseModel):
    source: EvidenceSource
    # Human-readable pointer: "bug 123 comment 0", "dom/base/nsINode.h:793@abc123".
    locator: str
    url: str | None = None
    # Source revision the evidence was read at, when it comes from code or VCS.
    revision: str | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    # True for items of the report snapshot the investigation started from, so
    # recovered information never blurs into what the report originally said.
    original: bool = False


class Evidence(BaseModel):
    id: str
    kind: str
    summary: str
    content: str
    provenance: Provenance
    action_id: str | None = None


class FactKind(StrEnum):
    OBSERVATION = "observation"
    HYPOTHESIS = "hypothesis"


class KnownFact(BaseModel):
    """A statement computed from an evidence item, such as a crash-stats reading."""

    id: str
    claim: str
    kind: FactKind
    category: str
    evidence_ids: list[str] = []
    origin: Literal["original_report", "recovered"] = "recovered"


class Inference(BaseModel):
    """The most likely answer to a gap the evidence did not settle.

    Never a fact and never closes a gap: it is shown as an inference, with its
    reasoning, the evidence it rests on, alternatives and a way to confirm it.
    """

    answer: str
    reasoning: str = ""
    evidence_ids: list[str] = []
    confidence: Level = Level.LOW
    alternatives: list[str] = []
    how_to_confirm: str = ""
    # False when it cites no valid evidence or names code found in no evidence;
    # the confidence is then capped at low.
    grounded: bool = True
    round: int = 0


class StatusChange(BaseModel):
    status: GapStatus
    reason: str
    step: int
    at: datetime = Field(default_factory=utcnow)


class InformationGap(BaseModel):
    id: str
    taxonomy_label: str
    question: str
    why_it_matters: str = ""
    relevance: Level = Level.MEDIUM
    status: GapStatus = GapStatus.CONFIRMED_MISSING
    origin: Literal["initial_detection", "late_discovery", "evidence_followup"] = (
        "initial_detection"
    )
    evidence_ids: list[str] = []
    # The established answer and the evidence that states it.
    finding: str | None = None
    finding_evidence_ids: list[str] = []
    confidence: Level | None = None
    investigation_history: list[str] = []
    # Lookups that served this gap.
    automatic_attempts: int = 0
    status_history: list[StatusChange] = []
    inference: Inference | None = None

    def set_status(self, status: GapStatus, reason: str, step: int) -> None:
        if status == self.status and self.status_history:
            return
        self.status = status
        self.status_history.append(
            StatusChange(status=status, reason=reason, step=step)
        )


class ActionRecord(BaseModel):
    """One lookup: what the agent asked, why, and what it gave."""

    id: str
    round: int
    step: int
    action: ActionType
    gap_id: str | None = None
    arguments: dict[str, Any] = {}
    rationale: str = ""
    # "fallback": run by the code rather than chosen by the model (the recall of
    # earlier bugs before the triage).
    decided_by: Literal["llm", "fallback"] = "llm"
    outcome: Literal["new_evidence", "no_new_evidence", "error", "blocked"] = (
        "no_new_evidence"
    )
    evidence_ids: list[str] = []
    error: str | None = None
    duration_s: float = 0.0


class ValidationRecord(BaseModel):
    """How a recorded answer fared against the evidence it cites."""

    gap_id: str
    step: int
    model_verdict: str
    final_verdict: str
    cited: list[str] = []
    # supported: cited evidence about this bug backs the answer; weak: it cites only
    # earlier bugs; unsupported: nothing valid cited, or it names code found in no
    # evidence.
    support: Literal["supported", "weak", "unsupported", "n/a"] = "n/a"
    note: str = ""


class InvestigationBudget(BaseModel):
    max_tool_calls: int = 30
    max_llm_calls: int = 150
    tool_calls_used: int = 0
    llm_calls_used: int = 0

    @property
    def tool_calls_left(self) -> int:
        return max(0, self.max_tool_calls - self.tool_calls_used)


class LLMCallRecord(BaseModel):
    stage: str
    provider: str
    model: str
    attempts: int
    ok: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_s: float = 0.0
    error: str | None = None


class RunMode(BaseModel):
    """Which components are enabled; the evaluation ablations toggle these."""

    # Lookups in the code, crash data and Bugzilla.
    investigation: bool = True
    # Inferred answers for the gaps the evidence does not settle.
    inference: bool = True
    # Earlier bugs fixed before the cutoff, and their fixes, as knowledge.
    knowledge: bool = True

    @property
    def name(self) -> str:
        """The components turned off ("no_lookups+no_knowledge"), or "full"."""
        off = [
            name
            for name, on in (
                ("no_lookups", self.investigation),
                ("no_inference", self.inference),
                ("no_knowledge", self.knowledge),
            )
            if not on
        ]
        return "+".join(off) or "full"


class StackFrame(BaseModel):
    index: int | None = None
    module: str | None = None
    function: str
    file: str | None = None
    line: int | None = None


class ReportIndicators(BaseModel):
    """Identifiers pulled deterministically (regexes) from the report text."""

    stack_frames: list[StackFrame] = []
    crash_ids: list[str] = []
    crash_reasons: list[str] = []
    files: list[str] = []
    identifiers: list[str] = []
    bug_refs: list[int] = []


class ReportComment(BaseModel):
    id: int
    count: int
    author: str
    created: datetime
    text: str
    evidence_id: str


class ReportAttachment(BaseModel):
    id: int
    created: datetime
    description: str
    content_type: str
    file_name: str
    is_patch: bool
    evidence_id: str


class ReportSnapshot(BaseModel):
    bug_id: int
    url: str
    summary: str
    product: str | None = None
    component: str | None = None
    status: str | None = None
    resolution: str | None = None
    fields: dict[str, Any] = {}
    crash_signatures: list[str] = []
    comments: list[ReportComment] = []
    attachments: list[ReportAttachment] = []
    # What the as-of cutoff withheld, for the leakage audit.
    hidden_after_cutoff: dict[str, int] = {}
    rolled_back_fields: list[str] = []
    captured_at: datetime = Field(default_factory=utcnow)


class RoundRecord(BaseModel):
    """The run that produced the investigation (one per investigation)."""

    round: int
    run_id: str | None = None
    llm: str
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    stop_reason: str | None = None


class FinalAssessment(BaseModel):
    assessment: Literal["resolved", "partially_resolved", "unresolved"]
    stop_reason: str
    summary: str
    requires_followup: bool
    counts: dict[str, int] = {}


class CrashInformationState(BaseModel):
    schema_version: str = SCHEMA_VERSION
    state_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    bug_id: int
    as_of: datetime | None = None
    source_revision: str | None = None
    taxonomy_version: str
    mode: RunMode = RunMode()
    report_snapshot: ReportSnapshot | None = None
    indicators: ReportIndicators = ReportIndicators()
    known_facts: list[KnownFact] = []
    evidence: list[Evidence] = []
    information_gaps: list[InformationGap] = []
    actions_taken: list[ActionRecord] = []
    validations: list[ValidationRecord] = []
    investigation_budget: InvestigationBudget = InvestigationBudget()
    llm_calls: list[LLMCallRecord] = []
    rounds: list[RoundRecord] = []
    step: int = 0
    final_assessment: FinalAssessment | None = None

    @property
    def round(self) -> int:
        return len(self.rounds)

    def gap(self, gap_id: str) -> InformationGap | None:
        return next((g for g in self.information_gaps if g.id == gap_id), None)

    def relevant_gaps(self) -> list[InformationGap]:
        """The pieces of missing information: every gap not set aside."""
        return [
            g for g in self.information_gaps if g.status != GapStatus.NOT_APPLICABLE
        ]

    def next_id(self, prefix: str) -> str:
        pools = {
            "ev": self.evidence,
            "fact": self.known_facts,
            "gap": self.information_gaps,
            "act": self.actions_taken,
        }
        return f"{prefix}-{len(pools[prefix]) + 1:03d}"
