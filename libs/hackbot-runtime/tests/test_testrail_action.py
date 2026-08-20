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
            "preconditions": "A PDF is available.",
            "steps": [
                {"action": "Open the PDF", "expectation": "The PDF is displayed."}
            ],
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
        "generated_test_cases": [
            {
                "id": 1,
                "title": "The PDF opens",
                "preconditions": "A PDF is available.",
                "steps": [
                    {"action": "Open the PDF", "expectation": "The PDF is displayed."}
                ],
            }
        ],
    }


async def test_submit_test_plan_tool_rejects_invalid_input():
    recorder = ActionsRecorder()

    with pytest.raises(ToolError) as exc:
        await testrail.submit_test_plan(
            recorder,
            feature=" ",
            generated_test_cases=[
                {
                    "id": 1,
                    "title": "Case",
                    "steps": [],
                }
            ],
        )

    assert "invalid TestRail submission" in str(exc.value)
    assert recorder.actions == []


async def test_submit_test_plan_tool_rejects_cases_without_expectation():
    recorder = ActionsRecorder()

    with pytest.raises(ToolError) as exc:
        await testrail.submit_test_plan(
            recorder,
            feature="Feature",
            generated_test_cases=[
                {
                    "id": 1,
                    "title": "Case",
                    "steps": [{"action": "Open the PDF", "expectation": None}],
                }
            ],
        )

    assert "invalid TestRail submission" in str(exc.value)
    assert recorder.actions == []


async def test_submit_test_plan_tool_rejects_non_sequential_case_ids():
    recorder = ActionsRecorder()
    cases = _cases()
    cases[0]["id"] = 2

    with pytest.raises(ToolError) as exc:
        await testrail.submit_test_plan(
            recorder,
            feature="Feature",
            generated_test_cases=cases,
        )

    assert "test case ids must be sequential starting at 1" in str(exc.value)
    assert recorder.actions == []


async def test_submit_test_plan_tool_rejects_a_second_submission():
    recorder = ActionsRecorder()
    await testrail.submit_test_plan(
        recorder, feature="Feature", generated_test_cases=_cases()
    )

    with pytest.raises(ToolError) as exc:
        await testrail.submit_test_plan(
            recorder, feature="Other feature", generated_test_cases=_cases()
        )

    assert "already recorded" in str(exc.value)
    assert [action["params"]["feature"] for action in recorder.actions] == ["Feature"]


async def test_submit_test_plan_tool_rejects_more_than_thirty_cases():
    recorder = ActionsRecorder()
    case = _cases()[0]
    cases = [{**case, "id": index} for index in range(1, 32)]

    with pytest.raises(ToolError) as exc:
        await testrail.submit_test_plan(
            recorder,
            feature="Feature",
            generated_test_cases=cases,
        )

    assert "invalid TestRail submission" in str(exc.value)
    assert recorder.actions == []


async def test_submit_test_plan_tool_preserves_blank_expectations():
    recorder = ActionsRecorder()

    await testrail.submit_test_plan(
        recorder,
        feature="Feature",
        generated_test_cases=[
            {
                "id": 1,
                "title": "Case",
                "steps": [
                    {"action": "Open the PDF", "expectation": ""},
                    {"action": "Select text", "expectation": "Text is selected."},
                ],
            }
        ],
    )

    assert recorder.actions[0]["params"]["generated_test_cases"][0]["steps"] == [
        {"action": "Open the PDF", "expectation": ""},
        {"action": "Select text", "expectation": "Text is selected."},
    ]


def test_submit_test_plan_handler_is_registered():
    assert isinstance(get_handler(ACTION_TYPE), SubmitTestPlanHandler)
