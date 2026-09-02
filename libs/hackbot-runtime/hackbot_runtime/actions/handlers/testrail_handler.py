"""Apply-side TestRail action for generated test cases."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from testrail_client import TestRailClient

from hackbot_runtime.actions.handlers.base import ActionResult, ApplyContext

log = logging.getLogger(__name__)

_CASE_TYPE_NAME = "Functional"
_CASE_TEMPLATE_NAME = "Test Case (Steps)"
_CASE_LABEL = "AI Generated"
_SECTION_NAME = "Test Cases"
_CASE_STATUSES = ("passed", "failed", "unsuitable")


@lru_cache(maxsize=1)
def _client() -> TestRailClient:
    return TestRailClient()


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


async def _resolve_case_type_id(client: TestRailClient) -> int:
    response = await client.get_case_types()
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


async def _resolve_template_id(client: TestRailClient) -> int:
    response = await client.get_templates()
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


async def _resolve_status_ids(client: TestRailClient) -> dict[str, int]:
    """Case status -> id of the TestRail status it is posted as.

    An unsuitable case is posted as Blocked, the closest status a result can
    carry; the other two match TestRail's own status names.
    """
    response = await client.get_statuses()
    statuses = response.get("statuses", []) if isinstance(response, dict) else response
    ids = {status["name"]: int(status["id"]) for status in statuses}
    return {
        "passed": ids["passed"],
        "failed": ids["failed"],
        "unsuitable": ids["blocked"],
    }


def _case_payload(
    test_case: dict[str, Any],
    case_type_id: int,
    template_id: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": test_case["title"],
        "type_id": case_type_id,
        "template_id": template_id,
        "labels": [_CASE_LABEL],
        "custom_steps_separated": _separated_steps(test_case),
    }
    if test_case.get("preconditions"):
        payload["custom_preconds"] = test_case["preconditions"]
    return payload


def _separated_steps(test_case: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "content": str(step["action"]),
            "expected": str(step.get("expectation") or ""),
        }
        for step in test_case.get("steps", [])
    ]


def _run_name() -> str:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"[Hackbot] Run Results - {stamp}"


def _result_comment(result: dict[str, Any]) -> str:
    summary = str(result.get("summary") or "")
    failure_reason = str(result.get("failure_reason") or "")
    if failure_reason:
        return f"{summary}\n\nFailure reason:\n{failure_reason}"
    return summary


def _executed_results(
    params: dict[str, Any],
    created_case_ids: dict[int, int],
    status_ids: dict[str, int],
) -> list[dict[str, Any]]:
    """One TestRail result per generated case, in generation order.

    ``SubmitTestPlanHandler.apply`` has already checked that every case carries a
    recognised status, so indexing here cannot silently drop one.
    """
    return [
        {
            "case_id": created_case_ids[int(test_case["id"])],
            "status_id": status_ids[test_case["result"]["status"]],
            "comment": _result_comment(test_case["result"]),
        }
        for test_case in params["generated_test_cases"]
    ]


def _run_payload(
    suite_id: int, case_ids: list[int], description: str
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": _run_name(),
        "include_all": False,
        "case_ids": case_ids,
        "suite_id": suite_id,
    }
    if description:
        payload["description"] = description
    return payload


class SubmitTestPlanHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        feature = str(params.get("feature") or "").strip()
        test_cases = params.get("generated_test_cases") or []
        summary = str(params.get("summary") or "")

        if not feature:
            return ActionResult.failed("TestRail submission requires a feature name")
        if not test_cases:
            return ActionResult.failed("TestRail submission requires test cases")
        if any(not test_case.get("result") for test_case in test_cases):
            return ActionResult.failed("TestRail submission requires execution results")
        if any(
            str(test_case["result"].get("status") or "") not in _CASE_STATUSES
            for test_case in test_cases
        ):
            return ActionResult.failed(
                "TestRail submission requires all cases to have executed results"
            )

        try:
            client = _client()
            case_type_id = await _resolve_case_type_id(client)
            template_id = await _resolve_template_id(client)
            # Before anything is created, so a missing status leaves no suite behind.
            status_ids = await _resolve_status_ids(client)
            suite_name = f"[Hackbot] - {feature}"
            suite_id = _require_id(
                await client.add_suite(suite_name),
                "suite",
            )
            section_id = _require_id(
                await client.add_section(suite_id, _SECTION_NAME),
                "section",
            )

            created_case_ids: dict[int, int] = {}
            for test_case in test_cases:
                generated_id = int(test_case["id"])
                case_id = _require_id(
                    await client.add_case(
                        section_id,
                        _case_payload(
                            test_case,
                            case_type_id,
                            template_id,
                        ),
                    ),
                    "case",
                )
                created_case_ids[generated_id] = case_id

            case_ids = list(created_case_ids.values())
            run_id = _require_id(
                await client.add_run(_run_payload(suite_id, case_ids, summary)),
                "run",
            )
            await client.add_results_for_cases(
                run_id,
                _executed_results(params, created_case_ids, status_ids),
            )

        except Exception as exc:
            log.exception(
                "Failed to submit test plan to TestRail for run %s", ctx.run_id
            )
            return ActionResult.failed(str(exc))

        return ActionResult.ok(
            {
                "suite_id": suite_id,
                "url": client.suite_url(suite_id),
                "section_id": section_id,
                "case_ids": case_ids,
                "run_id": run_id,
            }
        )
