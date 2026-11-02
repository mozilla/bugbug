"""Small shared TestRail API client."""

from typing import Any

import httpx

from testrail_client.config import TestRailSettings
from testrail_client.models import (
    TestRailCase,
    TestRailCaseType,
    TestRailCaseTypesResponse,
    TestRailResult,
    TestRailResultInput,
    TestRailResultsResponse,
    TestRailRun,
    TestRailSection,
    TestRailStatus,
    TestRailStatusesResponse,
    TestRailStep,
    TestRailSuite,
    TestRailTemplate,
    TestRailTemplatesResponse,
)


class TestRailClient:
    def __init__(self, settings: TestRailSettings | None = None) -> None:
        self.settings = settings or TestRailSettings.from_env()

    @property
    def base_url(self) -> str:
        return self.settings.url.rstrip("/")

    def suite_url(self, suite_id: int) -> str:
        return f"{self.base_url}/index.php?/suites/view/{suite_id}"

    def _api_url(self, endpoint: str) -> str:
        return f"{self.base_url}/index.php?/api/v2/{endpoint.lstrip('/')}"

    async def request(
        self, method: str, endpoint: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any] | list[Any]:
        async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
            response = await client.request(
                method,
                self._api_url(endpoint),
                auth=(self.settings.username, self.settings.api_key),
                json=data,
                headers={"Content-Type": "application/json"},
            )
        response.raise_for_status()
        if not response.content:
            return {}
        result = response.json()
        if not isinstance(result, (dict, list)):
            raise RuntimeError("TestRail returned an unexpected response")
        return result

    async def get_case_types(self) -> list[TestRailCaseType]:
        response = await self.request("GET", "get_case_types")
        return TestRailCaseTypesResponse.model_validate(response).root

    async def get_templates(self) -> list[TestRailTemplate]:
        response = await self.request(
            "GET", f"get_templates/{self.settings.project_id}"
        )
        return TestRailTemplatesResponse.model_validate(response).root

    async def get_statuses(self) -> list[TestRailStatus]:
        response = await self.request("GET", "get_statuses")
        return TestRailStatusesResponse.model_validate(response).root

    async def add_suite(self, name: str) -> TestRailSuite:
        response = await self.request(
            "POST",
            f"add_suite/{self.settings.project_id}",
            {"name": name},
        )
        return TestRailSuite.model_validate(response)

    async def add_section(self, suite_id: int, name: str) -> TestRailSection:
        response = await self.request(
            "POST",
            f"add_section/{self.settings.project_id}",
            {"suite_id": suite_id, "name": name},
        )
        return TestRailSection.model_validate(response)

    async def add_case(
        self,
        section_id: int,
        *,
        title: str,
        type_id: int,
        template_id: int,
        steps: list[TestRailStep],
        preconditions: str | None = None,
        labels: list[str] | None = None,
    ) -> TestRailCase:
        payload: dict[str, Any] = {
            "title": title,
            "type_id": type_id,
            "template_id": template_id,
            "custom_steps_separated": [step.model_dump() for step in steps],
        }
        if preconditions:
            payload["custom_preconds"] = preconditions
        if labels:
            payload["labels"] = labels
        response = await self.request("POST", f"add_case/{section_id}", payload)
        return TestRailCase.model_validate(response)

    async def add_run(
        self,
        *,
        suite_id: int,
        case_ids: list[int],
        name: str,
        description: str | None = None,
    ) -> TestRailRun:
        """Create a run holding only ``case_ids``, not every case in the suite."""
        payload: dict[str, Any] = {
            "name": name,
            "suite_id": suite_id,
            "include_all": False,
            "case_ids": case_ids,
        }
        if description:
            payload["description"] = description
        response = await self.request(
            "POST",
            f"add_run/{self.settings.project_id}",
            payload,
        )
        return TestRailRun.model_validate(response)

    async def add_results_for_cases(
        self, run_id: int, results: list[TestRailResultInput]
    ) -> list[TestRailResult]:
        response = await self.request(
            "POST",
            f"add_results_for_cases/{run_id}",
            {"results": [result.model_dump() for result in results]},
        )
        return TestRailResultsResponse.model_validate(response).root
