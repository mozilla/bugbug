import pytest
from agent_tools.registry import ToolError
from hackbot_runtime.actions import ActionsRecorder, testrail
from hackbot_runtime.actions.handlers import get_handler
from hackbot_runtime.actions.handlers.testrail_handler import SubmitTestPlanHandler
from hackbot_runtime.actions.testrail import ACTION_TYPE


def _cases():
    return [
        {
            "id": 1,
            "title": "The PDF opens",
            "context": "content",
            "preconditions": "A PDF is available.",
            "steps": ["Open the PDF"],
        }
    ]


async def test_submit_test_plan_tool_records_deferred_action():
    recorder = ActionsRecorder()

    message = await testrail.submit_test_plan(
        recorder, feature="Feature", generated_test_cases=_cases()
    )

    assert message == "Recorded testrail.submit_test_plan (#0)."
    assert recorder.actions[0]["type"] == ACTION_TYPE
    assert recorder.actions[0]["params"] == {
        "feature": "Feature",
        "generated_test_cases": _cases(),
    }


async def test_submit_test_plan_tool_rejects_invalid_input():
    recorder = ActionsRecorder()

    with pytest.raises(ToolError) as exc:
        await testrail.submit_test_plan(
            recorder,
            feature=" ",
            generated_test_cases=[{"id": 1, "title": "Case", "steps": []}],
        )

    assert "invalid TestRail submission" in str(exc.value)
    assert recorder.actions == []


def test_submit_test_plan_handler_is_registered():
    assert isinstance(get_handler(ACTION_TYPE), SubmitTestPlanHandler)
