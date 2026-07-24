"""TestRail-domain recordable actions.

The test plan generator records this action deterministically after its
structured result has been validated. The external TestRail mutation still
happens only in the apply side handler.
"""

from __future__ import annotations

from typing import Any

from hackbot_runtime.actions.recorder import ActionsRecorder

ACTION_TYPE = "testrail.submit_test_cases"


def record_test_plan(
    recorder: ActionsRecorder,
    test_plan: dict[str, Any],
    *,
    reasoning: str = "Upload the generated test cases to TestRail.",
) -> dict:
    """Record validated generated cases for deferred TestRail submission."""
    params = {
        "feature": test_plan["feature"],
        "generated_test_cases": test_plan["generated_test_cases"],
    }
    return recorder.record(ACTION_TYPE, params, reasoning=reasoning)
