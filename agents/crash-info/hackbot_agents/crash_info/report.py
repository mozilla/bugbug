"""The final assessment and the structured / developer-facing reports."""

import re
from typing import Any

from .models import CrashInformationState, FinalAssessment, GapStatus, Level
from .prompting import one_line
from .recovery import stack_frames_in, usable_stack
from .taxonomy import plain_name

# Recorded in every report: the engine that ran the investigation. Reports of
# an earlier, staged pipeline say "pipeline" or have no engine at all.
ENGINE = "claude-agent-sdk"
TAXONOMY_ORDER = {"Repair": 0, "Obj": 1, "Exec": 2, "Verif": 3, "Trigger": 4}


def assess(state: CrashInformationState, stop_reason: str) -> FinalAssessment:
    relevant = state.relevant_gaps()
    recovered = sum(1 for g in relevant if g.status == GapStatus.RECOVERED)
    if relevant and recovered == len(relevant):
        assessment = "resolved"
    elif recovered:
        assessment = "partially_resolved"
    else:
        assessment = "unresolved"
    unsettled = [g for g in relevant if g.status != GapStatus.RECOVERED]
    inferred = sum(1 for g in unsettled if g.inference)
    counts = {
        "relevant_gaps": len(relevant),
        "recovered": recovered,
        "unresolved": len(unsettled),
        "not_applicable": len(state.information_gaps) - len(relevant),
        "evidence_items": len(state.evidence),
        "tool_calls": state.investigation_budget.tool_calls_used,
        "inferred": inferred,
        "no_answer": len(unsettled) - inferred,
    }
    summary = (
        f"{len(relevant)} pieces of missing information: {recovered} established from "
        f"evidence, {inferred} inferred (not confirmed), "
        f"{counts['no_answer']} without an answer."
    )
    return FinalAssessment(
        assessment=assessment,
        stop_reason=stop_reason,
        summary=summary,
        requires_followup=bool(unsettled),
        counts=counts,
    )


def build_report(state: CrashInformationState, engine: str = ENGINE) -> dict[str, Any]:
    final = state.final_assessment
    snap = state.report_snapshot
    calls = state.llm_calls
    last_round = state.rounds[-1] if state.rounds else None
    return {
        "bug_id": state.bug_id,
        "title": snap.summary if snap else None,
        "state_id": state.state_id,
        "mode": state.mode.name,
        "engine": engine,
        "as_of": state.as_of.isoformat() if state.as_of else None,
        "source_revision": state.source_revision,
        "assessment": final.assessment if final else None,
        "stop_reason": final.stop_reason if final else None,
        "summary": final.summary if final else None,
        "requires_followup": final.requires_followup if final else None,
        "counts": final.counts if final else {},
        "gaps": [
            {
                "gap_id": g.id,
                "label": g.taxonomy_label,
                "question": g.question,
                "why_it_matters": g.why_it_matters,
                "relevance": g.relevance.value,
                "status": g.status.value,
                "origin": g.origin,
                "finding": g.finding,
                "evidence_ids": g.finding_evidence_ids or g.evidence_ids,
                "confidence": g.confidence.value if g.confidence else None,
                "automatic_attempts": g.automatic_attempts,
                "inference": g.inference.model_dump(mode="json")
                if g.inference
                else None,
            }
            for g in state.relevant_gaps()
        ],
        "not_applicable": [
            {
                "gap_id": g.id,
                "label": g.taxonomy_label,
                "question": g.question,
                "reason": g.status_history[-1].reason if g.status_history else "",
            }
            for g in state.information_gaps
            if g.status == GapStatus.NOT_APPLICABLE
        ],
        "known_facts": [
            {
                "fact_id": f.id,
                "claim": f.claim,
                "kind": f.kind.value,
                "category": f.category,
                "origin": f.origin,
                "evidence_ids": f.evidence_ids,
            }
            for f in state.known_facts
        ],
        "evidence": [
            {
                "id": e.id,
                "kind": e.kind,
                "summary": e.summary,
                "source": e.provenance.source.value,
                "locator": e.provenance.locator,
                "url": e.provenance.url,
                "revision": e.provenance.revision,
                "original": e.provenance.original,
            }
            for e in state.evidence
        ],
        "investigation": [
            {
                "step": a.step,
                "action": a.action.value,
                "gap_id": a.gap_id,
                "arguments": a.arguments,
                "decided_by": a.decided_by,
                "outcome": a.outcome,
                "evidence_ids": a.evidence_ids,
                "note": a.error,
                "rationale": a.rationale,
            }
            for a in state.actions_taken
        ],
        "validation": {
            "assessments": len(state.validations),
            "unsupported_claims": sum(
                1 for v in state.validations if v.support == "unsupported"
            ),
            "weakly_supported": sum(
                1 for v in state.validations if v.support == "weak"
            ),
        },
        "usage": {
            "llm": last_round.llm if last_round else None,
            "llm_calls": len(calls),
            "llm_attempts": sum(c.attempts for c in calls),
            "failed_llm_calls": sum(1 for c in calls if not c.ok),
            "input_tokens": sum(c.input_tokens or 0 for c in calls),
            "output_tokens": sum(c.output_tokens or 0 for c in calls),
            "llm_seconds": round(sum(c.latency_s for c in calls), 1),
            "tool_calls": state.investigation_budget.tool_calls_used,
        },
        "leakage_control": {
            "as_of": state.as_of.isoformat() if state.as_of else None,
            "hidden_after_cutoff": snap.hidden_after_cutoff if snap else {},
            "rolled_back_fields": snap.rolled_back_fields if snap else [],
            "code_revision": state.source_revision,
            "blocked_lookups": sum(
                1 for a in state.actions_taken if a.outcome == "blocked"
            ),
        },
    }


def render_markdown(state: CrashInformationState) -> str:
    report = build_report(state)
    snap = state.report_snapshot
    evidence = {e.id: e for e in state.evidence}

    def cite(ids: list[str]) -> str:
        links = []
        for i in ids:
            e = evidence.get(i)
            links.append(f"[{i}]({e.provenance.url})" if e and e.provenance.url else i)
        return ", ".join(links)

    title = f"bug {state.bug_id}" + (f": {snap.summary}" if snap else "")
    lines = [f"# CrashInfo: {title}", ""]
    meta = [
        f"**{(report['assessment'] or 'n/a').replace('_', ' ')}**",
        f"stop: {report['stop_reason']}",
    ]
    if state.as_of:
        meta.append(f"as of {state.as_of:%Y-%m-%d %H:%M} UTC")
    if state.source_revision:
        meta.append(f"code at `{state.source_revision[:12]}`")
    meta.append(f"brain: {report['usage']['llm']}")
    meta.append(f"mode: {report['mode']}")
    lines += [" · ".join(meta), "", report["summary"] or "", ""]

    lines += ["## Missing information", ""]
    gaps = sorted(
        state.relevant_gaps(),
        key=lambda g: (TAXONOMY_ORDER.get(g.taxonomy_label.split(".")[0], 9), g.id),
    )
    for n, gap in enumerate(gaps, 1):
        lines += [
            f"### {n}. {plain_name(gap.taxonomy_label)}",
            "",
            f"{gap.question} ({gap.taxonomy_label}, {gap.id}, "
            f"{gap.relevance.value} relevance)",
            "",
        ]
        if gap.why_it_matters:
            lines.append(f"- Why it matters: {gap.why_it_matters}")
        inf = gap.inference
        if gap.status == GapStatus.RECOVERED:
            refs = cite(gap.finding_evidence_ids)
            lines.append(f"- **Established** ({refs}): {gap.finding}")
        elif inf:
            flag = "" if inf.grounded else ", weakly grounded"
            lines.append(
                f"- **Inferred, not confirmed** ({inf.confidence.value} confidence{flag}): {inf.answer}"
            )
            lines.append(
                f"  - Why: {inf.reasoning} ({cite(inf.evidence_ids) or 'no evidence cited'})"
            )
            if inf.alternatives:
                lines.append("  - Alternatives: " + " / ".join(inf.alternatives))
            if inf.how_to_confirm:
                lines.append(f"  - To confirm: {inf.how_to_confirm}")
        else:
            lines.append("- No answer: neither the sources nor inference settled it.")
        lines.append("")

    if report["not_applicable"]:
        lines += [
            "<details><summary>Taxonomy labels assessed as not missing</summary>",
            "",
        ]
        lines += [
            f"- {plain_name(g['label'])}: {g['question']} {g['reason']} "
            f"({g['label']}, {g['gap_id']})"
            for g in report["not_applicable"]
        ]
        lines += ["", "</details>", ""]

    if report["investigation"]:
        lines += ["## Investigation log", ""]
        for a in report["investigation"]:
            args = ", ".join(f"{k}={v}" for k, v in a["arguments"].items())
            result = a["outcome"].replace("_", " ")
            if a["evidence_ids"]:
                result += f" {cite(a['evidence_ids'])}"
            note = f" ({a['note']})" if a["note"] else ""
            lines.append(
                f"{a['step']}. `{a['action']}` {args} for {a['gap_id'] or '-'}: {result}{note}"
            )
            # The agent's reasoning before the step.
            if a["rationale"]:
                lines.append(f"   _Thought: {one_line(a['rationale'], 400)}_")
        lines.append("")

    facts = [f for f in state.known_facts if f.origin != "original_report"]
    if facts:
        lines += ["## Recovered facts", ""]
        lines += [f"- {f.claim} ({cite(f.evidence_ids)})" for f in facts]
        lines.append("")

    lines += ["## Evidence", ""]
    for e in state.evidence:
        origin = "report" if e.provenance.original else e.provenance.source.value
        url = f" <{e.provenance.url}>" if e.provenance.url else ""
        lines.append(
            f"- **{e.id}** ({origin}: {e.provenance.locator}) {e.summary}{url}"
        )
    usage = report["usage"]
    lines += [
        "",
        f"_Model calls: {usage['llm_calls']} ({usage['llm_attempts']} attempts, "
        f"{usage['input_tokens']} in / {usage['output_tokens']} out tokens, {usage['llm_seconds']}s); "
        f"tool calls: {usage['tool_calls']}._",
    ]
    return "\n".join(lines) + "\n"


# Notes handed to an agent that fixes the bug (bug-fix as a chained run): only
# what the fix depends on and CrashInfo can support, the decision first. The
# notes say whether each is established or inferred.
HANDOFF_NOTES = 4
LEVEL_ORDER = {Level.HIGH: 0, Level.MEDIUM: 1, Level.LOW: 2}


def handoff_gaps(state: CrashInformationState) -> list:
    """Gaps established from evidence, or inferred with high confidence from cited
    evidence; not the test labels (Verif), which do not shape the fix."""
    kept = [
        g
        for g in state.relevant_gaps()
        if not g.taxonomy_label.startswith("Verif.")
        and (
            g.status == GapStatus.RECOVERED
            or (
                g.inference is not None
                and g.inference.confidence == Level.HIGH
                and g.inference.grounded
            )
        )
    ]
    kept.sort(
        key=lambda g: (
            TAXONOMY_ORDER.get(g.taxonomy_label.split(".")[0], 9),
            LEVEL_ORDER.get(g.relevance, 9),
            g.id,
        )
    )
    return kept[:HANDOFF_NOTES]


def _where(e) -> str:
    """Where a piece of evidence is, as a reader without the evidence list can
    find it: a file, a code search, a bug comment, a crash signature."""
    where = e.provenance.locator
    if e.provenance.source.value not in ("searchfox", "hgmo", "local_repo"):
        return where  # "bug 1 comment 2", "crash-stats [@ nsINode::OwnerDoc]"
    where = where.split("@")[0]  # without the revision
    return f"code {where}" if where.startswith("search ") else where.split(":")[0]


def recovered_crash(state: CrashInformationState) -> Any:
    """For a report without a stack, the first crash report CrashInfo found with
    a usable one, if any."""
    if state.indicators.stack_frames:
        return None
    for evidence in state.evidence:
        if evidence.kind == "crash_report" and usable_stack(
            stack_frames_in(evidence.content)
        ):
            return evidence
    return None


def answered_gaps(state: CrashInformationState) -> list:
    """Every relevant gap CrashInfo answered, established or inferred at any
    confidence, Verif included; in the notes' order (Verif last)."""
    kept = [
        g
        for g in state.relevant_gaps()
        if g.status == GapStatus.RECOVERED or g.inference is not None
    ]
    kept.sort(
        key=lambda g: (
            g.taxonomy_label.startswith("Verif."),  # how to check a fix: last
            TAXONOMY_ORDER.get(g.taxonomy_label.split(".")[0], 9),
            LEVEL_ORDER.get(g.relevance, 9),
            g.id,
        )
    )
    return kept


def render_handoff(state: CrashInformationState, full: bool = False) -> str:
    """CrashInfo's notes for the agent that writes the fix: a few hundred words
    where the full report runs to thousands. Evidence is named by where it is
    (file, comment), since that agent has no evidence list.

    ``full``: every answer instead of the few the fix depends on (any confidence,
    Verif too) and what is still unknown, without the report's table of contents,
    why-it-matters, alternatives or to-confirm advice."""
    evidence = {e.id: e for e in state.evidence}

    def named(text: str) -> str:
        return re.sub(
            r"`?\[?(ev-\d+)\]?`?",
            lambda m: f"`{_where(evidence[m[1]])}`" if m[1] in evidence else m[0],
            text,
        )

    def sources(ids: list[str]) -> str:
        return "; ".join(
            dict.fromkeys(_where(evidence[i]) for i in ids if i in evidence)
        )

    where = []
    if state.as_of:
        where.append(f"as of {state.as_of:%Y-%m-%d %H:%M} UTC")
    if state.source_revision:
        where.append(f"code at {state.source_revision[:12]}")
    kind = "report" if full else "notes"
    lines = [f"# CrashInfo {kind} for bug {state.bug_id} ({', '.join(where)})", ""]
    gaps = answered_gaps(state) if full else handoff_gaps(state)
    unknown = [
        g
        for g in state.relevant_gaps()
        if g.status != GapStatus.RECOVERED and g.inference is None
    ]
    crash = recovered_crash(state)
    if not gaps and crash is None and not (full and unknown):
        lines.append("CrashInfo found nothing to guide the fix.")
        return "\n".join(lines) + "\n"
    if crash is not None:
        # The agent that fixes the bug cannot reach crash-stats: what CrashInfo
        # found there reaches it only through these notes.
        frames = stack_frames_in(crash.content)
        fields = [
            line
            for line in crash.content.splitlines()
            if line.split(":", 1)[0] in ("reason", "address", "moz_crash_reason")
        ]
        lines += [
            "The bug report holds no stack; crash-stats, before the cutoff: "
            + crash.summary.removeprefix("crash report "),
            *(f"   {line}" for line in fields),
            "   Crashing thread:",
            *(f"   {frame}" for frame in frames[:8]),
            "",
        ]
    if gaps:
        lines += [
            (
                "Everything CrashInfo found that the bug report is missing"
                if full
                else "What the fix depends on"
            )
            + ", from the bug as it was before any fix. "
            "Established: shown by the sources named. Inferred: CrashInfo's "
            "conclusion from them, not confirmed.",
            "",
        ]
    for n, gap in enumerate(gaps, 1):
        lines.append(f"{n}. [{gap.taxonomy_label}] {gap.question}")
        if gap.status == GapStatus.RECOVERED:
            lines.append(
                f"   - Established: {named(gap.finding)} "
                f"(sources: {sources(gap.finding_evidence_ids)})"
            )
        else:
            inf = gap.inference
            how = f"{inf.confidence.value} confidence"
            if not inf.grounded:
                how += ", weakly grounded"
            lines.append(f"   - Inferred ({how}): {named(inf.answer)}")
            if inf.reasoning:
                lines.append(f"   - Basis: {named(inf.reasoning)}")
        lines.append("")
    if full and unknown:
        lines += [
            "Still unknown:",
            *(f"- [{g.taxonomy_label}] {g.question}" for g in unknown),
            "",
        ]
    return "\n".join(lines)
