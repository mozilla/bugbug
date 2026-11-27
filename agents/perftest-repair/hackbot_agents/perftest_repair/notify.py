"""The Slack report every finished run posts, recorded as an action.

A try push is linked through ``{{actions.<action id>.url}}``, which hackbot-api
fills in after applying the push.
"""

from __future__ import annotations

from hackbot_runtime import ActionsRecorder
from hackbot_runtime.actions.slack import HACKBOT_UI_URL
from hackbot_runtime.actions.try_server import TRY_PUSH_ACTION_TYPE

from .agent import PerftestRepairResult
from .config import MAX_TRY_FAILURE_RATE, REVISION_REVIEWERS, SLACK_CHANNEL

BUG_URL = "https://bugzilla.mozilla.org/show_bug.cgi?id={bug_id}"
RUN_URL = HACKBOT_UI_URL.rstrip("/") + "/runs/{run_id}"

# Slack truncates long messages badly; the full summary is in the run.
MAX_SUMMARY_CHARS = 1200

# Resolved by hackbot-api from the applied try push's result.
TRY_URL_PLACEHOLDER = "{{{{actions.{action_id}.url}}}}"


def try_push_of(recorder: ActionsRecorder) -> dict | None:
    """The run's last recorded try push action, if any."""
    pushes = [a for a in recorder.list_actions() if a["type"] == TRY_PUSH_ACTION_TYPE]
    return pushes[-1] if pushes else None


def revision_params(
    result: PerftestRepairResult, try_push: dict, announce_id: str
) -> dict:
    """The Phabricator revision hackbot-api opens once the try push has passed.

    ``try_push`` is the recorded try push action, and ``announce_id`` the ID of
    the recorded Slack report the try results are posted under.
    """
    summary = result.summary.strip()
    if summary.startswith("#"):
        summary = summary.partition("\n")[2].strip()
    try_id = try_push["action_id"]
    return {
        "bug_id": result.bug_id,
        "title": try_push["params"].get("title")
        or f"Bug {result.bug_id} - perf test fix",
        "summary": summary,
        "test_plan": f"Try push: {TRY_URL_PLACEHOLDER.format(action_id=try_id)}",
        "reviewers": REVISION_REVIEWERS,
        "wait_for_try": {
            "try_action_id": try_id,
            "max_failure_rate": MAX_TRY_FAILURE_RATE,
            "slack_channel": SLACK_CHANNEL,
            "slack_action_id": announce_id,
        },
    }


def _headline(result: PerftestRepairResult, try_push: dict | None) -> str:
    if result.outcome == "diagnosis":
        return "Diagnosis, no fix suggested"
    if try_push:
        return "Suggested fix, pushed to try"
    return "Suggested fix (unverified)"


def _try_line(try_push: dict) -> str:
    params = try_push["params"]
    tasks = ", ".join(f"`{task}`" for task in params.get("tasks") or []) or "auto"
    runs = params.get("rebuild") or 1
    url = TRY_URL_PLACEHOLDER.format(action_id=try_push["action_id"])
    return f"Try push ({runs}× {tasks}): {url}"


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
