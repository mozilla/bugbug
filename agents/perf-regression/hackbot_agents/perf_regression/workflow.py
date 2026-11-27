# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""The bounded experiment state machine, independent of model/tool adapters."""

from collections.abc import Awaitable, Callable

from .validation import evaluate


def format_report(proposal: dict | None, state: dict) -> str:
    """Render recorded analysis and CI verdicts for Hackbot's Markdown view."""
    sections = [f"**Outcome:** {state.get('summary', state['status'])}"]
    if proposal:
        for heading, field in (
            (
                "Candidate change" if proposal["has_candidate"] else "Investigation",
                "summary",
            ),
            ("Where the regression occurs", "regression_location"),
            ("Why the regression happened", "root_cause"),
            ("Evidence and limitations", "profile_evidence"),
        ):
            sections.append(
                f"## {heading}\n\n{proposal.get(field) or 'Not established.'}"
            )
        checks = proposal.get("correctness_checks", [])
        sections.append(
            "## Correctness checks\n\n"
            + ("\n".join(f"- {check}" for check in checks) or "No checks reported.")
        )
        # Only useful once it is known the candidate did not settle it.
        if state["status"] not in {"fixed", "running", "awaiting_ci"}:
            sections.append(
                "## Suggested next steps\n\n"
                + (proposal.get("next_steps") or "None suggested.")
            )
    attempts = state.get("attempts", [])
    if attempts:
        attempt = attempts[-1]
        validation = attempt.get("validation", {})
        lines = []
        if validation.get("failure_reason"):
            lines.append(validation["failure_reason"])
        for metric in validation.get("metrics", []):
            lines.append(
                f"- **{metric['metric']}**: {metric['status']}. {metric['reason']}"
            )
            if "counts" in metric:
                means = [
                    f"{metric[key]:.2f} {metric.get('unit', '')}".strip()
                    if metric.get(key) is not None
                    else "unavailable"
                    for key in ("good_mean", "bad_mean", "candidate_mean")
                ]
                lines.append(
                    "Means (before regression / regressed / candidate): "
                    + " / ".join(means)
                    + ". Independent jobs: "
                    + " / ".join(str(n) for n in metric["counts"])
                    + "."
                )
                if metric.get("improvement_pct") is not None:
                    lines.append(
                        f"Observed improvement over regressed mean: {metric['improvement_pct']:.2f}%."
                    )
        if not lines:
            lines.append(
                "CI recovery has not been established."
                if state["status"] == "blocked"
                else "CI validation is pending."
            )
        ci_url = attempt.get("ci", {}).get("url")
        if ci_url:
            lines.append(f"[Try results]({ci_url})")
        candidate_url = attempt.get("ci", {}).get("candidate_url")
        if candidate_url:
            lines.append(f"[Candidate patch as pushed to Try]({candidate_url})")
        sections.append("## Performance validation\n\n" + "\n\n".join(lines))
    return "\n\n".join(sections) + "\n"


async def run_attempts(
    rows: list[dict],
    *,
    max_attempts: int,
    propose: Callable[[int, list[dict]], Awaitable[dict]],
    historical_baselines: Callable[[], Awaitable[tuple[dict, dict]]],
    measure: Callable[[int, dict], Awaitable[dict]],
    checkpoint: Callable[[dict], None],
) -> dict:
    if max_attempts != 1:
        raise ValueError("max_attempts must be 1")
    attempts = []
    proposal = await propose(1, attempts)
    if not proposal["has_candidate"]:
        result = {
            "status": "no_solution",
            "summary": proposal["summary"],
            "attempts": attempts,
            "positive_attempts": [],
        }
        checkpoint(result)
        return result
    attempt = {"number": 1, "proposal": proposal, "status": "awaiting_ci"}
    attempts.append(attempt)
    checkpoint({"status": "running", "attempts": attempts})
    good, bad = await historical_baselines()
    measured = await measure(1, proposal)
    if measured.get("pending"):
        attempt["ci"] = measured
        result = {
            "status": "awaiting_ci",
            "summary": "Candidate pushed to Try; waiting for CI validation.",
            "attempts": attempts,
            "positive_attempts": [],
        }
        checkpoint(result)
        return result
    result = finish_attempt(rows, good, bad, attempt, measured)
    checkpoint(result)
    return result


def finish_attempt(
    rows: list[dict], good: dict, bad: dict, attempt: dict, measured: dict
) -> dict:
    """Evaluate saved evidence identically in local and event-driven runs."""
    proposal = attempt["proposal"]
    verdict = evaluate(rows, good, bad, measured["measurements"])
    if measured.get("failure_reason"):
        verdict.update(fixed=False, failure_reason=measured["failure_reason"])
    positive_attempts = [1] if verdict["positive"] else []
    attempt.update(
        status="not_verified"
        if measured.get("failure_reason")
        else "fixed"
        if verdict["fixed"]
        else "improved"
        if verdict["positive"]
        else "not_verified",
        ci=measured,
        validation=verdict,
    )
    summary = (
        "Completed one candidate; some metrics improved but recovery was not reached"
        if verdict["positive"]
        else "Completed one candidate; no metric improved"
    )
    if measured.get("failure_reason"):
        summary = measured["failure_reason"]
    elif any(m["status"] == "inconclusive" for m in verdict["metrics"]):
        summary = "Completed one candidate; performance validation is inconclusive"
    result = {
        "status": "fixed" if verdict["fixed"] else "exhausted",
        "summary": proposal["summary"] if verdict["fixed"] else summary,
        **({"winning_attempt": 1} if verdict["fixed"] else {}),
        "attempts": [attempt],
        "positive_attempts": positive_attempts,
    }
    return result
