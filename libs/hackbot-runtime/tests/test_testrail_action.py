from hackbot_runtime.actions import ActionsRecorder
from hackbot_runtime.actions.handlers import get_handler
from hackbot_runtime.actions.handlers.testrail_handler import SubmitTestCasesHandler
from hackbot_runtime.actions.testrail import ACTION_TYPE, record_test_plan


def test_record_test_plan_records_deferred_action():
    recorder = ActionsRecorder()
    plan = {"feature": "Feature", "generated_test_cases": [], "results": []}

    action = record_test_plan(recorder, plan)

    assert action["type"] == ACTION_TYPE
    assert action["params"] == {
        "feature": "Feature",
        "generated_test_cases": [],
    }
    assert recorder.actions == [action]


def test_submit_test_cases_handler_is_registered():
    assert isinstance(get_handler(ACTION_TYPE), SubmitTestCasesHandler)
