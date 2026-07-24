"""Apply-side TestRail action for generated test cases."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from hackbot_runtime.actions.handlers.base import ActionResult, ApplyContext

log = logging.getLogger(__name__)

_DEFAULT_TESTRAIL_URL = "https://mozilla.testrail.io"
_CASE_TYPE_NAME = "Functional"
_CASE_TEMPLATE_NAME = "Test Case (Steps)"
_CASE_LABEL = "AI Generated"
_SECTION_NAME = "Test Cases"
_TIMEOUT_SECONDS = 30


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def _base_url() -> str:
    return os.environ.get("TESTRAIL_URL", _DEFAULT_TESTRAIL_URL).rstrip("/")


def _required_int_env(name: str) -> int:
    raw_value = _required_env(name)
    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _project_id() -> int:
    return _required_int_env("TESTRAIL_PROJECT_ID")


def _api_url(endpoint: str) -> str:
    return f"{_base_url()}/index.php?/api/v2/{endpoint.lstrip('/')}"


def _api_request(
    method: str, endpoint: str, data: dict[str, Any] | None = None
) -> dict[str, Any] | list[Any]:
    response = requests.request(
        method,
        _api_url(endpoint),
        auth=(
            _required_env("TESTRAIL_USERNAME"),
            _required_env("TESTRAIL_API_KEY"),
        ),
        json=data,
        headers={"Content-Type": "application/json"},
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    if not response.content:
        return {}
    result = response.json()
    if not isinstance(result, (dict, list)):
        raise RuntimeError("TestRail returned an unexpected response")
    return result


def _require_id(response: object, object_name: str) -> int:
    if not isinstance(response, dict):
        raise RuntimeError(f"TestRail did not return a {object_name} object")
    value = response.get("id")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"TestRail did not return an id for the created {object_name}"
        ) from exc


def _resolve_case_type_id() -> int:
    response = _api_request("GET", "get_case_types")
    case_types = (
        response.get("case_types", []) if isinstance(response, dict) else response
    )
    for case_type in case_types:
        if (
            isinstance(case_type, dict)
            and str(case_type.get("name", "")).strip().casefold()
            == _CASE_TYPE_NAME.casefold()
        ):
            return _require_id(case_type, "case type")
    raise RuntimeError(f'TestRail has no case type named "{_CASE_TYPE_NAME}"')


def _resolve_template_id(project_id: int) -> int:
    response = _api_request("GET", f"get_templates/{project_id}")
    templates = (
        response.get("templates", []) if isinstance(response, dict) else response
    )
    for template in templates:
        if (
            isinstance(template, dict)
            and str(template.get("name", "")).strip().casefold()
            == _CASE_TEMPLATE_NAME.casefold()
        ):
            return _require_id(template, "template")
    raise RuntimeError(f'TestRail has no template named "{_CASE_TEMPLATE_NAME}"')


def _case_payload(
    test_case: dict[str, Any], case_type_id: int, template_id: int
) -> dict[str, Any]:
    steps = [str(step) for step in test_case.get("steps", [])]
    payload: dict[str, Any] = {
        "title": test_case["title"],
        "type_id": case_type_id,
        "template_id": template_id,
        "labels": [_CASE_LABEL],
        "custom_steps_separated": [{"content": step, "expected": ""} for step in steps],
    }
    if test_case.get("preconditions"):
        payload["custom_preconds"] = test_case["preconditions"]
    return payload


def _suite_url(suite_id: int) -> str:
    return f"{_base_url()}/index.php?/suites/view/{suite_id}"


class SubmitTestCasesHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        feature = str(params.get("feature") or "").strip()
        test_cases = params.get("generated_test_cases") or []

        if not feature:
            return ActionResult.failed("TestRail submission requires a feature name")
        if not test_cases:
            return ActionResult.failed("TestRail submission requires test cases")

        try:
            project_id = _project_id()
            case_type_id = _resolve_case_type_id()
            template_id = _resolve_template_id(project_id)
            suite_name = f"[Hackbot] - {feature}"
            suite_id = _require_id(
                _api_request(
                    "POST",
                    f"add_suite/{project_id}",
                    {"name": suite_name},
                ),
                "suite",
            )
            section_id = _require_id(
                _api_request(
                    "POST",
                    f"add_section/{project_id}",
                    {"suite_id": suite_id, "name": _SECTION_NAME},
                ),
                "section",
            )

            created_case_ids: dict[int, int] = {}
            for test_case in test_cases:
                generated_id = int(test_case["id"])
                case_id = _require_id(
                    _api_request(
                        "POST",
                        f"add_case/{section_id}",
                        _case_payload(test_case, case_type_id, template_id),
                    ),
                    "case",
                )
                created_case_ids[generated_id] = case_id

        except Exception as exc:
            log.exception(
                "Failed to submit test plan to TestRail for run %s", ctx.run_id
            )
            return ActionResult.failed(str(exc))

        return ActionResult.ok(
            {
                "suite_id": suite_id,
                "suite_url": _suite_url(suite_id),
                "section_id": section_id,
                "case_ids": list(created_case_ids.values()),
            }
        )
