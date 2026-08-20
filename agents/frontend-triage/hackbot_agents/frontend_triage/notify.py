"""The Slack message an auto-applied run sends to the owning team's channel.

Only a run that applies itself reports: at `confidence: high` a comment and a
`severity`/`keywords` change reach the bug with nobody in between, and the team that
owns the component has no other signal that it happened. A medium or low run wrote
nothing to the bug, so there is nothing to tell anyone.

Recorded as a ``slack.post_message`` action rather than posted from the run, so it is
visible in the hackbot UI before it lands and the apply step delivers it at most once
(see ``hackbot_runtime.actions.slack``, and ``agents/test-repair`` for the same shape).

Two lines: the bug, and the run. The channel already says which product and component
this is, the analysis is on the bug, and the detail is in the run -- so neither is
repeated here. Confidence is not reported either, since only a `high` run gets this
far. An S1 is the one thing worth pulling out of the bug, as it is the level someone
may need to act on today.
"""

from __future__ import annotations

import logging

from hackbot_runtime.actions.recorder import ActionsRecorder
from hackbot_runtime.actions.slack import HACKBOT_UI_URL, record_message

from .agent import FrontendTriageResult
from .config import SLACK_CHANNELS

logger = logging.getLogger(__name__)

BUG_URL = "https://bugzilla.mozilla.org/show_bug.cgi?id={bug_id}"
RUN_URL = HACKBOT_UI_URL.rstrip("/") + "/runs/{run_id}"

# The severity that gets a marker. S2-S4 are ordinary triage outcomes; an S1 is
# somebody's afternoon.
URGENT_SEVERITY = "S1"


def _link(url: str, label: str) -> str:
    return f"<{url}|{label}>"


def channel_for(product: str | None, component: str | None) -> str | None:
    """The channel that owns ``product :: component``, or None if none does.

    Fails closed on a component that is not in :data:`~.config.SLACK_CHANNELS`, and on
    either half being missing -- both values are the agent's report of what it read off
    Bugzilla, so a run that garbled them sends nothing rather than posting into a
    channel that did not ask for it.
    """
    if not product or not component:
        return None
    return SLACK_CHANNELS.get(f"{product.strip()} :: {component.strip()}")


def _bug_link(result: FrontendTriageResult) -> str:
    return _link(BUG_URL.format(bug_id=result.bug_id), f"Bug {result.bug_id}")


def _summary(result: FrontendTriageResult) -> str:
    return result.summary.strip() if result.summary else ""


def _is_urgent(result: FrontendTriageResult) -> bool:
    assessment = result.severity_assessment
    suggested = (assessment.suggested or "") if assessment else ""
    return suggested.strip().upper() == URGENT_SEVERITY


def build_message(result: FrontendTriageResult, *, run_id: str) -> str:
    headline = _bug_link(result)
    summary = _summary(result)
    if summary:
        headline += f" — {summary}"
    # The level is spelled out next to the emoji, so it still reads as an S1 for anyone
    # whose client does not render one.
    headline = f"*{headline}*"
    if _is_urgent(result):
        headline = f":red_circle: {headline} ({URGENT_SEVERITY})"

    return "\n".join(
        [
            headline,
            _link(RUN_URL.format(run_id=run_id), "frontend-triage run details"),
        ]
    )


def _severity_field(result: FrontendTriageResult) -> str | None:
    assessment = result.severity_assessment
    suggested = (assessment.suggested or "").strip() if assessment else ""
    if not suggested:
        return None
    value = suggested.upper()
    # The assessment's own confidence, not the run's: `rules/severity-assessment.md`
    # holds the field change back below `high`, so anything less is a suggestion the
    # bug did not receive, and reading it as applied would be wrong.
    confidence = (assessment.confidence or "").strip() if assessment else ""
    if confidence and confidence.lower() != "high":
        value += f" (suggested, {confidence.lower()} confidence)"
    return f"*Severity*\n{value}"


def _component_field(result: FrontendTriageResult) -> str | None:
    """Where the bug lives, for the channels that own more than one component."""
    if not result.product or not result.component:
        return None
    return f"*Component*\n{result.product.strip()} :: {result.component.strip()}"


def build_blocks(result: FrontendTriageResult, *, run_id: str) -> list[dict]:
    headline = f"*{_bug_link(result)}*"
    summary = _summary(result)
    if summary:
        headline += f"\n{summary}"
    if _is_urgent(result):
        headline = f":red_circle: *{URGENT_SEVERITY}* {headline}"

    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": headline}}
    ]

    fields = [
        field
        for field in (_severity_field(result), _component_field(result))
        if field is not None
    ]
    if fields:
        blocks.append(
            {
                "type": "section",
                "fields": [{"type": "mrkdwn", "text": field} for field in fields],
            }
        )

    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Triaged by frontend-triage · "
                    + _link(RUN_URL.format(run_id=run_id), "run details"),
                }
            ],
        }
    )
    return blocks


def record_notification(
    recorder: ActionsRecorder, result: FrontendTriageResult, *, run_id: str
) -> dict | None:
    """Record the run's Slack message, if it has one to send.

    Returns the recorded action, or None when nothing is reported -- the run did not
    mark itself safe to apply unattended, or its component has no channel. Lives here
    rather than in ``__main__`` so the whole decision is testable without a
    ``HackbotContext``.
    """
    if not result.auto_apply:
        logger.info(
            "Bug %s: not auto-applied, so nothing to report to Slack", result.bug_id
        )
        return None

    channel = channel_for(result.product, result.component)
    if channel is None:
        logger.info(
            "Bug %s: no Slack channel for %r :: %r; not reporting",
            result.bug_id,
            result.product,
            result.component,
        )
        return None

    logger.info("Bug %s: reporting triage to %s", result.bug_id, channel)
    return record_message(
        recorder,
        channel,
        build_message(result, run_id=run_id),
        blocks=build_blocks(result, run_id=run_id),
    )
