"""What one investigation returns: the state, the reports, and the result
Hackbot saves in ``summary.json``."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from .models import CrashInformationState

try:
    from hackbot_runtime import HackbotAgentResult
except ImportError:  # Standalone use, outside the bugbug workspace.

    class HackbotAgentResult(BaseModel):  # type: ignore[no-redef]
        """Mirror of ``hackbot_runtime.results.HackbotAgentResult``."""

        num_turns: int
        total_cost_usd: float | None = None


Progress = Callable[[str], None]


class CrashInfoResult(HackbotAgentResult):
    bug_id: int
    state_id: str
    assessment: str
    stop_reason: str
    summary: str
    requires_followup: bool
    llm: str
    gaps: list[dict[str, Any]] = []


@dataclass
class CrashInfoOutcome:
    state: CrashInformationState
    report: dict[str, Any]
    markdown: str
    result: CrashInfoResult
    # The agent's conversation, when the engine keeps one apart from the log.
    transcript: list[dict[str, Any]] | None = None


def build_result(
    state: CrashInformationState,
    report: dict[str, Any],
    *,
    llm: str,
    num_turns: int | None = None,
    cost_usd: float | None = None,
) -> CrashInfoResult:
    final = state.final_assessment
    assert final is not None
    return CrashInfoResult(
        num_turns=report["usage"]["llm_attempts"] if num_turns is None else num_turns,
        total_cost_usd=cost_usd,
        bug_id=state.bug_id,
        state_id=state.state_id,
        assessment=final.assessment,
        stop_reason=final.stop_reason,
        summary=final.summary,
        requires_followup=final.requires_followup,
        llm=llm,
        gaps=[
            {
                k: g[k]
                for k in (
                    "gap_id",
                    "label",
                    "status",
                    "question",
                    "finding",
                    "evidence_ids",
                )
            }
            for g in report["gaps"]
        ],
    )
