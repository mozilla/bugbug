"""One investigation of one crash bug, whatever drives the model.

The engine (the Claude Agent SDK in ``agent.py``) runs the model loop; this
module holds what does not depend on it:

- :func:`open_report` reads the bug as it was at the cutoff, pins the code and
  completes the crash data the report lacks: no model call;
- :func:`recall_prompt` brings in earlier bugs fixed before the cutoff that
  resemble this one, with their fixes (``knowledge.py``);
- the prompts: the system prompt, built from the taxonomy, and the task;
- the phases (triage, investigate, report) and their turn limits;
- :func:`conclude`, which closes the gaps left without an answer and assesses
  the round.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from .evidence import EvidenceStore
from .extraction import build_snapshot, report_digest, report_text
from .models import CrashInformationState, GapStatus, RoundRecord, utcnow
from .prompting import render
from .react_tools import ACTION_TOOLS, Phase, Say, Toolbox, describe_gap
from .report import assess
from .settings import Settings
from .sources import Sources
from .sources.bugzilla import cutoff_before_first_patch
from .taxonomy import Taxonomy, load_taxonomy

log = logging.getLogger(__name__)

# Consecutive turns without a tool call before the run is stopped.
MAX_REMINDERS = 2
# Turns allowed to produce an accepted triage.
TRIAGE_TURNS = 4
# Turns allowed to record the remaining answers once the lookups or turns are over.
FINAL_TURNS = 4

LOOKUP_METHOD = (
    "You have $lookups lookups in all; triage, add_gap, record_finding, read_evidence "
    "and finish use none. Start at the crash: read the crashing function at the "
    "reported line, then trace where the failing value comes from: who sets it, who "
    "clears it, in which order, on which thread, at which point of startup or "
    "shutdown. Prefer specific lookups (a function, a file and line) to broad "
    "searches; independent lookups may go together in one turn. Before each tool "
    "call, say in one or two sentences what you learned and what you check next."
)
NO_LOOKUPS = (
    "You have no lookup tools in this run: triage, record each gap's answer from the "
    "bug report alone, then finish."
)
STATUSES = (
    '- status "established": the cited evidence states it (code you read, crash data, '
    'a comment). "inferred": your most likely answer, from the evidence and from how '
    "Gecko code usually works; give your confidence, your reasoning, alternatives and "
    'how to confirm it. "unknown": you cannot tell. "not_applicable": the gap turned '
    "out not to matter (the answer says why)."
)
STATUSES_NO_INFERENCE = (
    '- status "established": the cited evidence states it (code you read, crash data, '
    'a comment); otherwise "unknown", or "not_applicable" when the gap turned out not '
    "to matter. Do not guess."
)
KNOWLEDGE = (
    "Earlier bugs: before triage you receive earlier crash bugs that resemble this one "
    "and were fixed before the cutoff, with the discussion that led to their fix and "
    "the fix itself; prior_bugs searches for more. Use them to see what information "
    "was missing in similar crashes and to infer it here by analogy, citing them. They "
    "are about other bugs: an answer about this bug is established only by evidence "
    "about this bug."
)
REMINDERS: dict[Phase, str] = {
    "triage": "Start with triage: assess every taxonomy label against the bug report.",
    "investigate": "You did not call a tool. Continue with a lookup for a gap, record "
    "a finding, or call finish.",
    "report": "Record your answers for the gaps still without one (record_finding), "
    "then call finish.",
}
RECOVERABLE = {
    "evidence": "the code and crash data",
    "either": "the code and crash data, or with developers",
    "developer": "developers, rarely in code or crash data",
}
WHEN = {"initial": "from the report", "late": "once the cause is known"}


@dataclass(kw_only=True)
class Investigation:
    """Everything a run shares; ``info`` is the investigation state."""

    info: CrashInformationState
    store: EvidenceStore
    sources: Sources
    toolbox: Toolbox
    settings: Settings
    say: Say
    before_first_patch: bool = False
    # A small local model: shorter prompts and observations.
    local: bool = False

    @property
    def max_turns(self) -> int:
        """Turns before the agent is asked to record its answers and finish."""
        budget = self.info.investigation_budget
        return min(2 * budget.max_tool_calls + 6, budget.max_llm_calls)


def new_state(
    bug_id: int,
    settings: Settings,
    taxonomy: Taxonomy,
    *,
    as_of: datetime | None,
    run_id: str | None,
    llm: str,
) -> CrashInformationState:
    """A fresh investigation state, with the configured budget and the run recorded."""
    info = CrashInformationState(
        bug_id=bug_id,
        as_of=as_of,
        taxonomy_version=taxonomy.version,
        mode=settings.mode,
    )
    start_round(info, settings, run_id, llm)
    return info


def start_round(
    state: CrashInformationState, settings: Settings, run_id: str | None, llm: str
) -> None:
    """Apply the configured budget and record the run."""
    budget = state.investigation_budget
    budget.max_tool_calls = settings.investigation.max_tool_calls
    budget.max_llm_calls = settings.investigation.max_llm_calls
    state.rounds.append(RoundRecord(round=state.round + 1, run_id=run_id, llm=llm))


def new_investigation(
    bug_id: int,
    settings: Settings,
    sources: Sources,
    *,
    as_of: datetime | None = None,
    before_first_patch: bool = False,
    run_id: str | None = None,
    llm: str,
    local: bool = False,
    say: Say | None = None,
) -> Investigation:
    taxonomy = load_taxonomy(settings.taxonomy_path)
    info = new_state(bug_id, settings, taxonomy, as_of=as_of, run_id=run_id, llm=llm)
    store = EvidenceStore(info)
    say = say or log.info
    return Investigation(
        info=info,
        store=store,
        sources=sources,
        toolbox=Toolbox(
            info,
            store,
            sources,
            taxonomy,
            say,
            settings.investigation.max_gaps,
            crash_data=settings.investigation.crash_data,
            answer_review=settings.investigation.answer_review,
        ),
        settings=settings,
        say=say,
        before_first_patch=before_first_patch,
        local=local,
    )


async def open_report(inv: Investigation) -> list[str]:
    """Read the bug as of the cutoff and pin the code; no model call. Returns the
    opening user messages: the task with the report, then the crash data the
    report lacks, when there is some."""
    info = inv.info
    if inv.before_first_patch and info.as_of is None:
        info.as_of = cutoff_before_first_patch(
            await inv.sources.bugzilla.view(info.bug_id)
        )
        if info.as_of is None:
            raise ValueError(
                f"bug {info.bug_id} has no patch or landing to cut off before"
            )
    when = f" as of {info.as_of:%Y-%m-%d %H:%M} UTC" if info.as_of else ""
    inv.say(f"[read_report] bug {info.bug_id}{when}")
    view = await inv.sources.bugzilla.view(info.bug_id, info.as_of)
    build_snapshot(info, view, inv.store, inv.sources.bugzilla.bug_url(info.bug_id))
    if info.mode.investigation:
        info.source_revision = await inv.sources.pin_code(
            info.as_of, info.source_revision
        )
    snap = info.report_snapshot
    assert snap is not None
    hidden = snap.hidden_after_cutoff
    inv.say(
        f"      {len(info.evidence)} report items, {len(info.indicators.stack_frames)} stack frames"
        + (f"; code at {info.source_revision[:12]}" if info.source_revision else "")
        + (f"; withheld after cutoff: {hidden}" if any(hidden.values()) else "")
    )
    prompts = [task_prompt(inv)]
    if info.mode.investigation and (crash := await inv.toolbox.complete_stack()):
        prompts.append(crash_data_prompt(inv, crash))
    return prompts


async def recall_prompt(inv: Investigation) -> str | None:
    """Earlier bugs fixed before the cutoff that resemble this one, with their
    fixes; no model call, no lookup used. None when recall is off."""
    limit = inv.settings.investigation.prior_bugs_recalled
    if not inv.info.mode.knowledge or limit <= 0:
        return None
    ids = await inv.toolbox.recall(limit)
    return knowledge_prompt(inv, ids)


def conclude(inv: Investigation, stop_reason: str) -> None:
    """Close the gaps left without an answer and assess the round."""
    info = inv.info
    for gap in info.information_gaps:
        if gap.status in (GapStatus.CONFIRMED_MISSING, GapStatus.INVESTIGATING):
            gap.set_status(
                GapStatus.UNRESOLVED, f"round ended: {stop_reason}", info.step
            )
    gaps = inv.toolbox.open_gaps()
    inv.say(f"[finalize] {len(gaps)} pieces of missing information")
    for gap in gaps:
        inv.say(f"      {gap.id} [{gap.taxonomy_label}] {gap.question}")
        inv.say(f"        {describe_gap(gap)}")
    info.final_assessment = assess(info, stop_reason)
    info.rounds[-1].finished_at = utcnow()
    info.rounds[-1].stop_reason = stop_reason
    inv.say(
        f"[done] {info.final_assessment.assessment}: {info.final_assessment.summary}"
    )


# --- Prompts ----------------------------------------------------------------------


def system_prompt(inv: Investigation) -> str:
    info = inv.info
    rev = info.source_revision
    if info.as_of:
        cutoff = (
            f"Time cutoff: you see bug {info.bug_id} as it was on "
            f"{info.as_of:%Y-%m-%d %H:%M} UTC, before any fix. Later comments, "
            "attachments and field changes are withheld"
            + (
                f"; code tools read mozilla-central at revision {rev[:12]}, the last "
                "push before the cutoff, and refuse later changesets and crash reports"
                if rev
                else ""
            )
            + ". Do not use any memory of how this bug was eventually fixed."
        )
    else:
        cutoff = f"You see bug {info.bug_id} as it is now" + (
            f"; code tools read mozilla-central at revision {rev[:12]}." if rev else "."
        )
    taxonomy = inv.toolbox.taxonomy
    return render(
        "react_system",
        bug_id=info.bug_id,
        cutoff=cutoff,
        lookups=LOOKUP_METHOD.replace(
            "$lookups", str(info.investigation_budget.max_tool_calls)
        )
        if info.mode.investigation
        else NO_LOOKUPS,
        version=taxonomy.version,
        taxonomy=taxonomy_guide(taxonomy, brief=inv.local),
        statuses=STATUSES if info.mode.inference else STATUSES_NO_INFERENCE,
        knowledge=KNOWLEDGE if info.mode.knowledge else "",
    )


def taxonomy_guide(taxonomy: Taxonomy, *, brief: bool = False) -> str:
    """Every category and label, with how its information is usually recovered:
    the lookups that serve it, where it usually lives, and when it can be assessed.
    ``brief`` uses the one-line decision card per label (for small local models)."""
    blocks = []
    for category in taxonomy.categories:
        blocks.append(f"{category.id}: {category.name}. {category.focus}")
        for label in taxonomy.in_category(category.id):
            lookups = ", ".join(t for a in label.actions for t in ACTION_TOOLS[a])
            practice = (
                f"  In practice: usual lookups {lookups}; the information usually "
                f"lives in {RECOVERABLE[label.recoverable_by]}; assess it "
                f"{WHEN[label.phase]}."
            )
            if brief:
                blocks.append(f"{label.card_line()}\n{practice}")
            else:
                nearby = f"  Nearby labels: {', '.join(label.nearby)}\n"
                blocks.append(f"{label.full_text()}\n{nearby}{practice}")
    return "\n\n".join(blocks)


def knowledge_prompt(inv: Investigation, evidence_ids: list[str]) -> str:
    if not evidence_ids:
        return (
            "Earlier bugs: no crash bug fixed before the cutoff resembles this one "
            "closely enough. prior_bugs can search with other terms."
        )
    each = 1_200 if inv.local else 3_500
    cases = "\n\n".join(inv.toolbox.item(i, each) for i in evidence_ids)
    return (
        "Earlier bugs: crash bugs fixed before the cutoff that resemble this one (by "
        "crash signature, crashing functions and component), with the discussion that "
        "led to their fix and the fix itself. Use them to see what information was "
        "missing in similar crashes and to infer it here, citing their ids; they "
        "support inferences about this bug, not established facts.\n\n" + cases
    )


def crash_data_prompt(inv: Investigation, evidence_ids: list[str]) -> str:
    each = 1_200 if inv.local else 3_500
    items = "\n\n".join(inv.toolbox.item(i, each) for i in evidence_ids)
    return (
        "Crash data the report lacks: it names crash signatures but holds no stack. "
        "Crash-stats, before the cutoff (cite these ids like the report's; they are "
        "evidence, not report items):\n\n" + items
    )


def task_prompt(inv: Investigation) -> str:
    info = inv.info
    return render(
        "react_task",
        digest=report_digest(info),
        when=f" as of {info.as_of:%Y-%m-%d %H:%M} UTC" if info.as_of else "",
        report=report_text(info, inv.store, 8_000 if inv.local else 40_000),
    )
