"""One run: preflight, triage, stability check, then record the writes.

The model makes exactly one judgement here -- the triage skill's. Everything that
decides what reaches the bug is code: the write mode (preflight), whether the
rating is stable enough to apply unattended (the calculator re-scored over the
inputs the bug left open), and the comment text. hackbot-api applies the recorded
actions only when ``auto_apply`` is true; otherwise they wait for a person.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from claude_agent_sdk import McpServerConfig
from hackbot_runtime import HackbotAgentResult
from hackbot_runtime.actions import ActionsRecorder
from performance_tools.triage import TriageResult
from performance_tools.triage.stability import check_stability

from . import preflight
from .comment import render_comment
from .hooks import REVIEW_FLAGS, add_comment_hook, update_bug_hook
from .preflight import WriteMode

TriageFn = Callable[[list[str]], Awaitable[TriageResult]]


class PerfTriageFindings(HackbotAgentResult):
    triage: TriageResult | None = None
    stability: dict | None = None
    write_mode: WriteMode = "full"
    skipped_reason: str | None = None
    # This run's verdict on whether its recorded actions may reach the bug with
    # nobody in between. hackbot-api reads it as findings.auto_apply.
    auto_apply: bool = False


async def run_perf_triage(
    *,
    bug_id: int,
    bugzilla_mcp_server: McpServerConfig,
    recorder: ActionsRecorder,
    triage: TriageFn,
    fetch_bug=preflight.fetch_bug,
    fetch_comments=preflight.fetch_comments,
    fetch_attachments=preflight.fetch_attachments,
    num_turns: Callable[[], int] = lambda: 0,
    total_cost_usd: Callable[[], float | None] = lambda: None,
) -> PerfTriageFindings:
    bug = await fetch_bug(bugzilla_mcp_server, bug_id)
    # Fail closed: preflight returns {} on a broker failure, and an empty bug
    # would read as unrated -- letting a confident run overwrite a person's rating.
    if bug.get("id") != bug_id or "cf_performance_impact" not in bug:
        return PerfTriageFindings(num_turns=0, skipped_reason="bug lookup failed")
    comments = await fetch_comments(bugzilla_mcp_server, bug_id)
    mode = preflight.write_mode(bug)
    existing = bug.get("cf_performance_impact")
    recorder.add_hook("bugzilla.add_comment", add_comment_hook(recorder, bug_id))
    recorder.add_hook("bugzilla.update_bug", update_bug_hook(recorder, bug_id, mode))

    attachments = await fetch_attachments(bugzilla_mcp_server, bug_id)
    result = await triage(preflight.profile_urls(bug, comments, attachments))

    def findings(**kw) -> PerfTriageFindings:
        return PerfTriageFindings(
            num_turns=num_turns(),
            total_cost_usd=total_cost_usd(),
            triage=result,
            write_mode=mode,
            **kw,
        )

    if not result.ok:
        return findings(skipped_reason=f"triage failed: {result.failure_reason}")

    stability = check_stability(result.selections, result.unestablished)
    stability_dict = {
        "confident": stability.confident,
        "bands": sorted(stability.bands),
        "flipping": list(stability.flipping),
        "unknown": list(stability.unknown),
    }

    if mode == "comment_only" and result.impact == existing:
        return findings(
            stability=stability_dict, skipped_reason="agrees with existing rating"
        )

    current = f"{bug.get('product', '')} :: {bug.get('component', '')}"
    text = render_comment(
        result, stability, mode, current_component=current, existing_impact=existing
    )
    recorder.record(
        "bugzilla.add_comment",
        {"bug_id": bug_id, "text": text, "is_private": False},
        reasoning=f"Performance triage: {result.impact} ({result.score})",
    )
    if mode == "full":
        changes: dict = {"cf_performance_impact": result.impact}
        if result.keywords:
            changes["keywords"] = {"add": list(result.keywords)}
        if result.impact == "high":
            changes["flags"] = REVIEW_FLAGS
        recorder.record(
            "bugzilla.update_bug",
            {"bug_id": bug_id, "changes": changes},
            reasoning="Set from the triage calculator's rating and keywords",
        )
    return findings(stability=stability_dict, auto_apply=stability.confident)
