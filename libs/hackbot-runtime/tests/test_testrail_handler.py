"""Tests for the apply-side TestRail action handler."""

import pytest
from hackbot_runtime.actions.handlers import ApplyContext, testrail_handler


@pytest.fixture(autouse=True)
def configure_project(monkeypatch):
    monkeypatch.setenv("TESTRAIL_PROJECT_ID", "73")


def _ctx():
    async def download(_key):
        raise AssertionError("TestRail submissions do not use artifacts")

    return ApplyContext(run_id="run-1", download_artifact=download)


def _plan():
    return {
        "feature": "PDF Improvements",
        "generated_test_cases": [
            {
                "id": 1,
                "title": "The PDF opens",
                "context": "content",
                "preconditions": "A PDF is available.",
                "steps": ["Open the PDF", "Select some text"],
            },
            {
                "id": 2,
                "title": "The toolbar remains available",
                "context": "chrome",
                "preconditions": None,
                "steps": ["Open the toolbar"],
            },
        ],
        "results": [
            {
                "id": 1,
                "status": "passed",
                "summary": "The PDF behaved as expected.",
                "failure_reason": None,
                "step_results": [
                    {
                        "step_number": 1,
                        "status": "passed",
                        "observation": "The PDF opened.",
                        "failure_reason": None,
                    },
                    {
                        "step_number": 2,
                        "status": "passed",
                        "observation": "Text was selected.",
                        "failure_reason": None,
                    },
                ],
            },
            {
                "id": 2,
                "status": "unsuitable",
                "summary": "The toolbar could not be inspected.",
                "failure_reason": "No available tool can inspect it.",
                "step_results": [
                    {
                        "step_number": 1,
                        "status": "not_run",
                        "observation": "Not run.",
                        "failure_reason": None,
                    }
                ],
            },
        ],
        "summary": "One passed and one was unsuitable.",
    }


async def test_submit_test_cases_creates_suite_section_and_cases(monkeypatch):
    calls = []
    responses = iter(
        [
            [{"id": 6, "name": "Functional"}],
            [{"id": 2, "name": "Test Case (Steps)"}],
            {"id": 10},
            {"id": 20},
            {"id": 101},
            {"id": 102},
        ]
    )

    def fake_request(method, endpoint, data=None):
        calls.append((method, endpoint, data))
        return next(responses)

    monkeypatch.setattr(testrail_handler, "_api_request", fake_request)
    monkeypatch.setattr(
        testrail_handler, "_base_url", lambda: "https://testrail.example"
    )

    result = await testrail_handler.SubmitTestCasesHandler().apply(_plan(), _ctx())

    assert result.status == "applied"
    assert result.result == {
        "suite_id": 10,
        "suite_url": "https://testrail.example/index.php?/suites/view/10",
        "section_id": 20,
        "case_ids": [101, 102],
    }

    assert calls[0] == ("GET", "get_case_types", None)
    assert calls[1] == ("GET", "get_templates/73", None)
    assert calls[2] == (
        "POST",
        "add_suite/73",
        {"name": "[Hackbot] - PDF Improvements"},
    )
    assert calls[3] == (
        "POST",
        "add_section/73",
        {"suite_id": 10, "name": "Test Cases"},
    )

    first_case = calls[4]
    assert first_case[1] == "add_case/20"
    assert first_case[2] == {
        "title": "The PDF opens",
        "type_id": 6,
        "template_id": 2,
        "labels": ["AI Generated"],
        "custom_preconds": "A PDF is available.",
        "custom_steps_separated": [
            {"content": "Open the PDF", "expected": ""},
            {"content": "Select some text", "expected": ""},
        ],
    }


async def test_submit_test_cases_reports_api_failure(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("TestRail is unavailable")

    monkeypatch.setattr(testrail_handler, "_api_request", fail)
    result = await testrail_handler.SubmitTestCasesHandler().apply(_plan(), _ctx())

    assert result.status == "failed"
    assert result.error == "TestRail is unavailable"


def test_api_request_uses_basic_authentication(monkeypatch):
    monkeypatch.setenv("TESTRAIL_URL", "https://testrail.example/")
    monkeypatch.setenv("TESTRAIL_USERNAME", "qa@example.com")
    monkeypatch.setenv("TESTRAIL_API_KEY", "secret")
    captured = {}

    class Response:
        content = b'{"id": 10}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": 10}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return Response()

    monkeypatch.setattr(testrail_handler.requests, "request", fake_request)

    result = testrail_handler._api_request("POST", "add_suite/73", {"name": "Suite"})

    assert result == {"id": 10}
    assert captured["url"] == (
        "https://testrail.example/index.php?/api/v2/add_suite/73"
    )
    assert captured["auth"] == ("qa@example.com", "secret")
    assert captured["json"] == {"name": "Suite"}
    assert captured["timeout"] == 30


def test_base_url_defaults_to_mozilla_testrail(monkeypatch):
    monkeypatch.delenv("TESTRAIL_URL", raising=False)

    assert testrail_handler._base_url() == "https://mozilla.testrail.io"


def test_api_request_requires_credentials(monkeypatch):
    monkeypatch.delenv("TESTRAIL_USERNAME", raising=False)

    try:
        testrail_handler._api_request("GET", "get_projects")
    except RuntimeError as exc:
        assert str(exc) == "TESTRAIL_USERNAME is not configured"
    else:
        raise AssertionError("missing TestRail configuration did not fail")


def test_project_id_can_be_overridden(monkeypatch):
    monkeypatch.setenv("TESTRAIL_PROJECT_ID", "99")

    assert testrail_handler._project_id() == 99


def test_project_id_is_required(monkeypatch):
    monkeypatch.delenv("TESTRAIL_PROJECT_ID")

    try:
        testrail_handler._project_id()
    except RuntimeError as exc:
        assert str(exc) == "TESTRAIL_PROJECT_ID is not configured"
    else:
        raise AssertionError("missing TestRail project id did not fail")


def test_configured_ids_must_be_integers(monkeypatch):
    monkeypatch.setenv("TESTRAIL_PROJECT_ID", "grave-yard")

    try:
        testrail_handler._project_id()
    except RuntimeError as exc:
        assert str(exc) == "TESTRAIL_PROJECT_ID must be an integer"
    else:
        raise AssertionError("invalid TestRail project id did not fail")


def test_resolve_case_type_id_by_name(monkeypatch):
    monkeypatch.setattr(
        testrail_handler,
        "_api_request",
        lambda *_args: [
            {"id": 3, "name": "Automated"},
            {"id": 12, "name": " functional "},
        ],
    )

    assert testrail_handler._resolve_case_type_id() == 12


def test_resolve_case_type_id_requires_functional_type(monkeypatch):
    monkeypatch.setattr(
        testrail_handler,
        "_api_request",
        lambda *_args: [{"id": 3, "name": "Automated"}],
    )

    try:
        testrail_handler._resolve_case_type_id()
    except RuntimeError as exc:
        assert str(exc) == 'TestRail has no case type named "Functional"'
    else:
        raise AssertionError("missing Functional case type did not fail")


def test_resolve_template_id_by_name(monkeypatch):
    monkeypatch.setattr(
        testrail_handler,
        "_api_request",
        lambda *_args: [
            {"id": 1, "name": "Test Case (Text)"},
            {"id": 2, "name": " test case (steps) "},
        ],
    )

    assert testrail_handler._resolve_template_id(73) == 2


def test_resolve_template_id_requires_steps_template(monkeypatch):
    monkeypatch.setattr(
        testrail_handler,
        "_api_request",
        lambda *_args: [{"id": 1, "name": "Test Case (Text)"}],
    )

    try:
        testrail_handler._resolve_template_id(73)
    except RuntimeError as exc:
        assert str(exc) == 'TestRail has no template named "Test Case (Steps)"'
    else:
        raise AssertionError("missing Test Case (Steps) template did not fail")
