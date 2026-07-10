"""Structured result reporting for the autowebcompat-repro agent."""

from __future__ import annotations

import imghdr
from pathlib import Path
from typing import Generic, Literal, TypeVar

from claude_agent_sdk import McpServerConfig, create_sdk_mcp_server, tool
from pydantic import BaseModel, Field, ValidationError, field_validator

RESULT_SERVER_NAME = "autowebcompat-repro"
SUBMIT_RESULT_TOOL = f"mcp__{RESULT_SERVER_NAME}__submit_result"

ResultT = TypeVar("ResultT", bound=BaseModel)


class ResultCollector(Generic[ResultT]):
    """Holds the result submitted by the agent, if any."""

    def __init__(self, result_cls: type[ResultT]) -> None:
        self._result_cls: type[ResultT] = result_cls
        self.result: ResultT | None = None


class TestPlanResult(BaseModel):
    is_webcompat: bool = Field(
        description=("true if the input describes a webcompat issue, otherwise false."),
    )

    affects_platforms: list[
        Literal["ios"] | Literal["android"] | Literal["desktop"]
    ] = Field(description="List of platforms which seem to be affected by the issue")

    affects_os: (
        None
        | Literal["all"]
        | list[Literal["windows"] | Literal["linux"] | Literal["macos"]]
    ) = Field(
        description="""List of desktop issues known to be affected.
        - `null` if the issue does not affect desktop.
        - "all" if there is no strong evidence that the issue is platform specific"
        - Otherwise a list of platform names which are likely affected
        """
    )

    affects_channels: list[Literal["nightly"] | Literal["stable"] | Literal["esr"]] = (
        Field(
            description="""List of channels affected
        - "esr" if the issue is reported as specific to ESR builds.
        - "stable" if the issue is reported as reproducing on stable builds, or there is no evidence for which channels are affected
        - "nightly" if the issue is reported as reproducing on nightly builds, or there is no evidence for which channels are affected
        """
        )
    )


class ReproductionResult(BaseModel):
    reproduced: bool = Field(
        description=(
            "true if the reported issue reproduced in Firefox, otherwise false."
        ),
    )

    failure_reason: (
        Literal["not_reproducable"]
        | Literal["non_compat"]
        | Literal["unsupported_platform"]
        | Literal["blocked"]
        | Literal["blocked_captcha"]
        | Literal["blocked_geo"]
        | Literal["login"]
        | Literal["down"]
        | Literal["other"]
        | None
    ) = Field(
        description="""If an issue was reproduced then `null`. When an issue could not be reproduced, one of
        following categories describing the reason for the failure:
          * not_reproducable - When it was possible to run all the steps to reproduce, but no issue was found
          * non_compat - When the report doesn't refer to site breakage for example for issues with the Firefox UI or product features such as reader mode
          * unsupported_platform - When the report is specific to a platform that isn't available e.g. iOS
          * blocked_captcha - When access to the site was blocked because the page requires solving a captcha
          * blocked_geo - When access to the site was blocked based on location ("geoblocking")
          * blocked - When access to the site was blocked for some reason that couldn't be identified as a captcha or geoblocking
          * login - When reproducing the issue requires completing a login flow
          * down - When the site down or unavailable in a way that is unrelated to the issue report
          * other - When the issue could not be reproduced for some other reason (please give details in the summary text)
"""
    )

    screenshot_path: Path | None = Field(
        description=(
            """The file path you saved a screenshot to via the `screenshot_page`
            `saveTo` parameter, showing the issue. Use the exact path you passed
            as `saveTo` (do NOT paste image data). This must only be set for
            issues where the breakage is visual in nature i.e. incorrect site
            layout rather than broken interaction. Otherwise it must be null."""
        ),
    )

    @field_validator("screenshot_path", mode="after")
    @classmethod
    def validate_screenshot_path(cls, path: Path | None) -> Path | None:
        if path is None:
            return None

        if not path.exists():
            raise ValueError(f"Screenshot path {path} doesn't exist")
        if imghdr.what(str(path)) != "png":
            raise ValueError(f"Screenshot path {path} is not a valid PNG image")
        return path


class BugReproductionResult(ReproductionResult):
    """Canonical result the agent produces for a web-compat investigation."""

    summary: str = Field(
        description="""A concise account of whether the issue represents a real
        webcompat issue i.e. it can be reproduced in Firefox."""
    )

    steps: str = Field(
        description=(
            "The ordered steps you took, as a single numbered list (1., 2., 3., "
            "... one step per line), written so another agent could reproduce "
            "them with no extra context. Each step must be self-contained: "
            "whenever you introduce an input or artifact the report did not "
            "provide (a file, image, account, or any other test data), state its "
            "exact origin — the URL you fetched it from, the command you ran, or "
            'how you generated it — not just that you "used" or "saved" it. A '
            "reader must be able to obtain the same inputs. Omit the reproduction "
            "screenshot step."
        ),
    )


class ChromeMaskResult(BaseModel):
    chrome_mask_fixed: bool | None = Field(
        description=(
            "Whether enabling the Chrome Mask extension (spoofing a Chrome "
            "User-Agent) fixed the reported behavior: true if it fixed it, "
            "false if it did not, null if the Chrome Mask test was not run "
            "(e.g. the issue did not reproduce at baseline)."
        ),
    )


def build_result_server(collector: ResultCollector) -> McpServerConfig:
    """Build an in-process MCP server exposing the ``submit_result`` tool.

    The handler validates the payload against :class:`ReproductionResult` and stores
    it on ``collector``. A validation error is returned to the model (as tool
    output) so it can correct and resubmit rather than failing the run.
    """

    @tool(
        "submit_result",
        "Submit the final web-compatibility investigation result. Call exactly "
        "once, at the end, after completing the investigation.",
        {
            **collector._result_cls.model_json_schema(),
            "additionalProperties": False,
        },
    )
    async def submit_result(args: dict) -> dict:
        try:
            collector.result = collector._result_cls.model_validate(args)
        except ValidationError as exc:
            return {
                "content": [{"type": "text", "text": f"Invalid result: {exc}"}],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": "Result recorded."}]}

    return create_sdk_mcp_server(name=RESULT_SERVER_NAME, tools=[submit_result])
