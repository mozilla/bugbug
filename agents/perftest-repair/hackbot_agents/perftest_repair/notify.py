"""The Slack report every finished run posts, recorded as an action.

A try push is linked through ``{{actions.try.url}}``, which hackbot-api fills in
after applying the push.
"""

from __future__ import annotations

from hackbot_runtime import ActionsRecorder
from hackbot_runtime.actions.slack import HACKBOT_UI_URL
from hackbot_runtime.actions.try_server import TRY_PUSH_ACTION_TYPE

from .agent import PerftestRepairResult
from .config import (
    MAX_TRY_FAILURE_RATE,
    REVISION_REVIEWERS,
    SLACK_CHANNEL,
    SLACK_REF,
    TRY_REF,
)

BUG_URL = "https://bugzilla.mozilla.org/show_bug.cgi?id={bug_id}"
RUN_URL = HACKBOT_UI_URL.rstrip("/") + "/runs/{run_id}"

# Slack truncates long messages badly; the full summary is in the run.
MAX_SUMMARY_CHARS = 1200

# Resolved by hackbot-api from the applied try push's result.
TRY_URL_PLACEHOLDER = "{{actions.%s.url}}" % TRY_REF


def try_push_of(recorder: ActionsRecorder) -> dict | None:
    """The params of the run's try push, if the agent recorded one under TRY_REF."""
    for action in recorder.actions:
        if action["type"] == TRY_PUSH_ACTION_TYPE and action.get("ref") == TRY_REF:
            return action["params"]
    return None


def revision_params(result: PerftestRepairResult, try_push: dict) -> dict:
    """The Phabricator revision hackbot-api opens once the try push has passed."""
    summary = result.summary.strip()
    if summary.startswith("#"):
        summary = summary.partition("\n")[2].strip()
    return {
        "bug_id": result.bug_id,
        "title": try_push.get("title") or f"Bug {result.bug_id} - perf test fix",
        "summary": summary,
        "test_plan": f"Try push: {TRY_URL_PLACEHOLDER}",
        "reviewers": REVISION_REVIEWERS,
        "wait_for_try": {
            "ref": TRY_REF,
            "max_failure_rate": MAX_TRY_FAILURE_RATE,
            "slack_channel": SLACK_CHANNEL,
            "slack_ref": SLACK_REF,
        },
    }


def _headline(result: PerftestRepairResult, try_push: dict | None) -> str:
    if result.outcome == "diagnosis":
        return "Diagnosis, no fix suggested"
    if try_push:
        return "Suggested fix, pushed to try"
    return "Suggested fix (unverified)"


def _try_line(try_push: dict) -> str:
    tasks = ", ".join(f"`{task}`" for task in try_push.get("tasks") or []) or "auto"
    runs = try_push.get("rebuild") or 1
    return f"Try push ({runs}× {tasks}): {TRY_URL_PLACEHOLDER}"


def build_message(
    result: PerftestRepairResult,
    *,
    run_id: str | None,
    try_push: dict | None = None,
) -> str:
    # A push of an empty tree tests nothing, so it is only reported with a fix.
    try_push = try_push if result.outcome == "fix" else None
    bug_link = f"<{BUG_URL.format(bug_id=result.bug_id)}|Bug {result.bug_id}>"
    lines = [
        f"*{bug_link}: {_headline(result, try_push)}*",
        f"{result.harness} · {result.failure_type} · cause: {result.cause} · "
        f"confidence {result.confidence:.0%}",
    ]
    summary = result.summary.strip()
    if summary.startswith("#"):
        # Drop a leading markdown heading such as "# Summary".
        summary = summary.partition("\n")[2].strip()
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS].rstrip() + "…"
    if summary:
        lines += ["", summary]
    if try_push:
        lines += ["", _try_line(try_push)]
    if run_id:
        target = "the patch and analysis" if result.outcome == "fix" else "the analysis"
        lines += ["", f"<{RUN_URL.format(run_id=run_id)}|Hackbot run>: {target}"]
    return "\n".join(lines)
