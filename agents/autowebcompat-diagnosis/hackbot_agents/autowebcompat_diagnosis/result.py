"""Structured result reporting for the autowebcompat-diagnosis agent."""

from __future__ import annotations

from pathlib import Path
from typing import Generic, Literal, TypeVar

from claude_agent_sdk import McpServerConfig, create_sdk_mcp_server, tool
from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

RESULT_SERVER_NAME = "autowebcompat-diagnosis"
SUBMIT_RESULT_TOOL = f"mcp__{RESULT_SERVER_NAME}__submit_result"

ResultT = TypeVar("ResultT", bound=BaseModel)


class ResultCollector(Generic[ResultT]):
    """Holds the result submitted by the agent, if any."""

    def __init__(self, result_cls: type[ResultT]) -> None:
        self._result_cls: type[ResultT] = result_cls
        self.result: ResultT | None = None


class DiagnosisPlanResult(BaseModel):
    """What the later tasks need, gathered before any browser is installed."""

    firefox_channel: Literal["nightly"] | Literal["stable"] | Literal["esr"] = Field(
        description=("The Firefox channel to diagnose on."),
    )

    channel_rationale: str = Field(
        description=(
            "One or two sentences on why you chose that channel, citing what "
            "you based it on (the `autowebcompat-repro-channels` marker, the "
            "report text, or the absence of both)."
        ),
    )

    url: str = Field(
        description="The URL of the page the issue was reported on.",
    )

    steps: str = Field(
        description=(
            "The steps to reproduce the issue, as a single numbered list (1., "
            "2., 3., ... one step per line), taken from the report and written "
            "so another agent could follow them with no extra context. Each "
            "step must be self-contained: whenever a step involves an input the "
            "report did not provide, state its exact origin. Always fill this "
            "in, even when a reproduction script is attached — the script may "
            "turn out not to work."
        ),
    )

    script_path: Path | None = Field(
        description=(
            "The file path you downloaded the attached Puppeteer reproduction "
            "script to, or null if the bug has no such attachment. Use the "
            "exact path you were given to write to (do NOT paste the script "
            "source)."
        ),
    )

    @field_validator("script_path", mode="after")
    @classmethod
    def validate_script_path(cls, path: Path | None) -> Path | None:
        if path is None:
            return None

        if not path.exists():
            raise ValueError(f"Script path {path} doesn't exist")
        if not path.read_text().strip():
            raise ValueError(f"Script path {path} is empty")
        return path


class ReproScriptResult(BaseModel):
    """Verdict from the script task: can the issue still be reproduced?"""

    reproduced: bool = Field(
        description=(
            "true if you confirmed the reported issue still reproduces in "
            "Firefox but not in Chrome, whether via a Puppeteer script or by "
            "driving the site with the DevTools tools. false if you could not "
            "reproduce it."
        ),
    )

    failure_reason: (
        Literal["not_reproducable"]
        | Literal["non_compat"]
        | Literal["blocked"]
        | Literal["blocked_captcha"]
        | Literal["blocked_geo"]
        | Literal["login"]
        | Literal["down"]
        | Literal["other"]
        | None
    ) = Field(
        description="""Null if the issue reproduced. Otherwise the category
        describing why it did not:
          * not_reproducable - all the steps ran, but the reported issue did not occur
          * non_compat - the behavior is identical in Firefox and Chrome, so this
          is not a Firefox web-compat issue
          * blocked_captcha - the site required solving a captcha
          * blocked_geo - the site blocked access based on location
          * blocked - access was blocked for a reason that isn't a captcha or geoblocking
          * login - reproducing requires completing a login flow
          * down - the site is down or unavailable, unrelated to the report
          * other - some other reason (give details in the summary)
""",
    )

    summary: str = Field(
        description=(
            "A concise account of what you did and what you observed in each "
            "browser, including why reproduction failed if it did."
        ),
    )

    script_path: Path | None = Field(
        description=(
            "The file path of the Puppeteer script that demonstrates the "
            "difference — Firefox exits 1 and Chrome exits 0. Use the exact "
            "path you were given to write to (do NOT paste the script source). "
            "Null if no script validated; that is acceptable and does not by "
            "itself mean the issue failed to reproduce."
        ),
    )

    @field_validator("script_path", mode="after")
    @classmethod
    def validate_script_path(cls, path: Path | None) -> Path | None:
        if path is None:
            return None

        if not path.exists():
            raise ValueError(f"Script path {path} doesn't exist")
        if not path.read_text().strip():
            raise ValueError(f"Script path {path} is empty")
        return path

    @model_validator(mode="after")
    def validate_consistency(self) -> ReproScriptResult:
        if not self.reproduced and self.script_path is not None:
            raise ValueError(
                "script_path must be null when reproduced is false; a script "
                "that does not demonstrate the issue is not a confirmation."
            )
        if self.reproduced and self.failure_reason is not None:
            raise ValueError("failure_reason must be null when reproduced is true")
        if not self.reproduced and self.failure_reason is None:
            raise ValueError("failure_reason is required when reproduced is false")
        return self


class DiagnosisResult(BaseModel):
    """The agent's root-cause account of why Firefox differs from Chrome."""

    root_cause: str = Field(
        description=(
            """Your root-cause hypothesis for why the site behaves differently in
            Firefox: what the page does, which behavior it depends on, and why
            that produces the reported breakage in Firefox but not Chrome. Be
            specific about the mechanism (e.g. the API, CSS property, or
            user-agent check involved). Where the behavior is related to a browser
            engine difference covered by a web-standard such as HTML or CSS
            then if possible provide links to the relevant parts of the specification
            document that define the behaviour. Skip these links if you don't know the
            right specification or section. Do not propose a fix."""
        ),
    )

    evidence: str = Field(
        description=(
            "The concrete observations supporting the hypothesis: console "
            "errors, network requests, DOM or computed-style measurements, "
            "feature-detection results, and what the reduced testcase showed in "
            "each browser. Be brief, this will be read by a busy engineer."
            "Cite what you actually observed, not what you expect."
        ),
    )
    testcase_path: Path | None = Field(
        description=(
            "The file path of the reduced HTML testcase you wrote. Set this only "
            "if you loaded it in both browsers and confirmed it shows the same "
            "difference as the real site. Use the exact path you were given to "
            "write to (do NOT paste the HTML source). Null if you could not "
            "produce a reduced testcase that reproduces the difference."
        ),
    )

    @field_validator("testcase_path", mode="after")
    @classmethod
    def validate_testcase_path(cls, path: Path | None) -> Path | None:
        if path is None:
            return None

        if not path.exists():
            raise ValueError(f"Testcase path {path} doesn't exist")
        if not path.read_text().strip():
            raise ValueError(f"Testcase path {path} is empty")
        return path


def build_result_server(collector: ResultCollector) -> McpServerConfig:
    """Build an in-process MCP server exposing the ``submit_result`` tool.

    The handler validates the payload against the collector's result class and
    stores it. A validation error is returned to the model (as tool output) so
    it can correct and resubmit rather than failing the run.
    """

    @tool(
        "submit_result",
        "Submit the final result for this task. Call exactly once, at the end, "
        "after completing the task.",
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
