"""Tests for the apply-side TestRail action handler."""

from hackbot_runtime.actions.handlers import ApplyContext, testrail_handler


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
                "steps": [
                    {"action": "Open the PDF", "expectation": None},
                    {
                        "action": "Select some text",
                        "expectation": "Text selection is highlighted in the PDF.",
                    },
                ],
            },
            {
                "id": 2,
                "title": "The toolbar remains available",
                "context": "chrome",
                "preconditions": None,
                "steps": [
                    {
                        "action": "Open the toolbar",
                        "expectation": "The toolbar remains visible and usable.",
                    },
                ],
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


class _FakeClient:
    def __init__(self, responses=None):
        self.calls = []
        self._responses = list(
            responses
            or [
                [{"id": 6, "name": "Functional"}],
                [{"id": 2, "name": "Test Case (Steps)"}],
                {"id": 10},
                {"id": 20},
                {"id": 101},
                {"id": 102},
            ]
        )

    def _next(self):
        value = self._responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def get_case_types(self):
        self.calls.append(("get_case_types",))
        return self._next()

    async def get_templates(self):
        self.calls.append(("get_templates",))
        return self._next()

    async def add_suite(self, name):
        self.calls.append(("add_suite", name))
        return self._next()

    async def add_section(self, suite_id, name):
        self.calls.append(("add_section", suite_id, name))
        return self._next()

    async def add_case(self, section_id, payload):
        self.calls.append(("add_case", section_id, payload))
        return self._next()

    def suite_url(self, suite_id):
        return f"https://testrail.example/index.php?/suites/view/{suite_id}"


async def test_submit_test_plan_creates_suite_section_and_cases(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(testrail_handler, "_client", lambda: client)

    result = await testrail_handler.SubmitTestPlanHandler().apply(_plan(), _ctx())

    assert result.status == "applied"
    assert result.result == {
        "suite_id": 10,
        "url": "https://testrail.example/index.php?/suites/view/10",
        "section_id": 20,
        "case_ids": [101, 102],
    }

    assert client.calls[0] == ("get_case_types",)
    assert client.calls[1] == ("get_templates",)
    assert client.calls[2] == ("add_suite", "[Hackbot] - PDF Improvements")
    assert client.calls[3] == ("add_section", 10, "Test Cases")

    first_case = client.calls[4]
    assert first_case[0:2] == ("add_case", 20)
    assert first_case[2] == {
        "title": "The PDF opens",
        "type_id": 6,
        "template_id": 2,
        "labels": ["AI Generated"],
        "custom_preconds": "A PDF is available.",
        "custom_steps_separated": [
            {"content": "Open the PDF", "expected": ""},
            {
                "content": "Select some text",
                "expected": "Text selection is highlighted in the PDF.",
            },
        ],
    }


def test_separated_steps_maps_expectations_from_step_objects():
    assert testrail_handler._separated_steps(
        {
            "steps": [
                {"action": "Open the PDF", "expectation": None},
                {"action": "Select text", "expectation": ""},
                {"action": "Copy text", "expectation": "Text is copied."},
            ],
        }
    ) == [
        {"content": "Open the PDF", "expected": ""},
        {"content": "Select text", "expected": ""},
        {"content": "Copy text", "expected": "Text is copied."},
    ]


async def test_submit_test_plan_reports_api_failure(monkeypatch):
    client = _FakeClient(responses=[RuntimeError("TestRail is unavailable")])
    monkeypatch.setattr(testrail_handler, "_client", lambda: client)

    result = await testrail_handler.SubmitTestPlanHandler().apply(_plan(), _ctx())

    assert result.status == "failed"
    assert result.error == "TestRail is unavailable"


async def test_submit_test_plan_requires_feature(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(testrail_handler, "_client", lambda: client)
    plan = _plan()
    plan["feature"] = " "

    result = await testrail_handler.SubmitTestPlanHandler().apply(plan, _ctx())

    assert result.status == "failed"
    assert result.error == "TestRail submission requires a feature name"
    assert client.calls == []


async def test_submit_test_plan_requires_cases(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(testrail_handler, "_client", lambda: client)
    plan = _plan()
    plan["generated_test_cases"] = []

    result = await testrail_handler.SubmitTestPlanHandler().apply(plan, _ctx())

    assert result.status == "failed"
    assert result.error == "TestRail submission requires test cases"
    assert client.calls == []


async def test_resolve_case_type_id_by_name():
    client = _FakeClient(
        responses=[
            [
                {"id": 3, "name": "Automated"},
                {"id": 12, "name": " functional "},
            ]
        ]
    )

    assert await testrail_handler._resolve_case_type_id(client) == 12


async def test_resolve_case_type_id_requires_functional_type():
    client = _FakeClient(responses=[[{"id": 3, "name": "Automated"}]])

    try:
        await testrail_handler._resolve_case_type_id(client)
    except RuntimeError as exc:
        assert str(exc) == 'TestRail has no case type named "Functional"'
    else:
        raise AssertionError("missing Functional case type did not fail")


async def test_resolve_template_id_by_name():
    client = _FakeClient(
        responses=[
            [
                {"id": 1, "name": "Test Case (Text)"},
                {"id": 2, "name": " test case (steps) "},
            ]
        ]
    )

    assert await testrail_handler._resolve_template_id(client) == 2


async def test_resolve_template_id_requires_steps_template():
    client = _FakeClient(responses=[[{"id": 1, "name": "Test Case (Text)"}]])

    try:
        await testrail_handler._resolve_template_id(client)
    except RuntimeError as exc:
        assert str(exc) == 'TestRail has no template named "Test Case (Steps)"'
    else:
        raise AssertionError("missing Test Case (Steps) template did not fail")
