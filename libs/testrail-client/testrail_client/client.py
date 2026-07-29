"""Small shared TestRail API client."""

from __future__ import annotations

from typing import Any

import httpx

from testrail_client.config import TestRailSettings


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

    async def get_case_types(self) -> dict[str, Any] | list[Any]:
        return await self.request("GET", "get_case_types")

    async def get_templates(self) -> dict[str, Any] | list[Any]:
        return await self.request("GET", f"get_templates/{self.settings.project_id}")

    async def add_suite(self, name: str) -> dict[str, Any] | list[Any]:
        return await self.request(
            "POST",
            f"add_suite/{self.settings.project_id}",
            {"name": name},
        )

    async def add_section(self, suite_id: int, name: str) -> dict[str, Any] | list[Any]:
        return await self.request(
            "POST",
            f"add_section/{self.settings.project_id}",
            {"suite_id": suite_id, "name": name},
        )

    async def add_case(
        self, section_id: int, payload: dict[str, Any]
    ) -> dict[str, Any] | list[Any]:
        return await self.request("POST", f"add_case/{section_id}", payload)
