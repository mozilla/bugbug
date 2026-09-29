"""Structured result reporting for the autowebcompat-intervention agent."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Generic, Literal, TypeVar

from claude_agent_sdk import McpServerConfig, create_sdk_mcp_server, tool
from pydantic import BaseModel, Field, ValidationError, field_validator

RESULT_SERVER_NAME = "autowebcompat-intervention"
SUBMIT_RESULT_TOOL = f"mcp__{RESULT_SERVER_NAME}__submit_result"

ResultT = TypeVar("ResultT", bound=BaseModel)


class ResultCollector(Generic[ResultT]):
    """Holds the result submitted by the agent, if any."""

    def __init__(self, result_cls: type[ResultT]) -> None:
        self._result_cls: type[ResultT] = result_cls
        self.result: ResultT | None = None


class ReproScriptResult(BaseModel):
    """The verdict from running the report's reproduction script.

    Produced by Python, not by an agent: the script either demonstrates the
    breakage or it does not, and that decides whether the run goes on to write
    an intervention.
    """

    reproduced: bool
    summary: str


class InterventionPlanResult(BaseModel):
    """What the planning stage worked out before anything is built or edited."""

    url: Annotated[
        str,
        Field(
            description=(
                "The affected URL, as specific as the report allows -- the page "
                "the breakage happens on, not just the site's homepage."
            )
        ),
    ]

    issue_details: Annotated[
        str,
        Field(
            description=(
                "Everything from the report and its comments that the next "
                "agent needs to write the intervention, written so it can work "
                "with no extra context: the broken behavior a user sees, the "
                "conditions it happens under, and any evidence or cause given "
                "in the description or comments (console errors, UA sniffing, "
                "failing requests, triage or diagnosis findings), including "
                "where it was stated. Where later comments supersede earlier "
                "ones, give the current state."
            )
        ),
    ]

    affects_platforms: Annotated[
        list[Literal["ios"] | Literal["android"] | Literal["desktop"]],
        Field(description="List of platforms which seem to be affected by the issue"),
    ]

    affects_os: Annotated[
        (
            None
            | Literal["all"]
            | list[Literal["windows"] | Literal["linux"] | Literal["macos"]]
        ),
        Field(
            description="""List of desktop issues known to be affected.
        - `null` if the issue does not affect desktop.
        - "all" if there is no strong evidence that the issue is OS specific"
        - Otherwise a list of OS names which are likely affected
        """
        ),
    ]

    script_path: Annotated[
        Path | None,
        Field(
            description=(
                "The file path you downloaded the attached Puppeteer reproduction "
                "script to, or null if the bug has no such attachment. Use the "
                "exact path you were given to write to (do NOT paste the script "
                "source)."
            ),
        ),
    ]

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


class InterventionResult(BaseModel):
    """What the agent concluded, and what it proved."""

    wrote_intervention: Annotated[
        bool,
        Field(
            description=(
                "true if you wrote or modified intervention files, false if you "
                "stopped before that (e.g. the bug did not reproduce)."
            )
        ),
    ]

    fixed_with_intervention: Annotated[
        bool,
        Field(
            description=(
                "true only if, after enabling interventions, you re-checked the "
                "same site and the breakage was gone. False if unverified."
            )
        ),
    ]

    build_succeeded: Annotated[
        bool,
        Field(
            description=(
                "true if the artifact build completed with your intervention in "
                "place. A failed build usually means the intervention JSON did "
                "not pass intervention_schema.json validation in codegen.py."
            )
        ),
    ]

    files_changed: Annotated[
        list[str],
        Field(
            default_factory=list,
            description=(
                "Repo-relative paths you created or edited, e.g. "
                "'browser/extensions/webcompat/data/interventions/"
                "1234567-example.com.json'."
            ),
        ),
    ]

    failure_reason: Annotated[
        (
            Literal["not_reproducible"]
            | Literal["no_puppeteer_script"]
            | Literal["no_intervention_possible"]
            | Literal["build_failed"]
            | Literal["verification_failed"]
            | Literal["unsupported_android"]
            | Literal["unsupported_ios"]
            | Literal["blocked"]
            | Literal["blocked_captcha"]
            | Literal["blocked_geo"]
            | Literal["login"]
            | Literal["down"]
            | Literal["headless"]
            | Literal["other"]
            | None
        ),
        Field(
            default=None,
            description="""Null if you delivered a verified intervention. Otherwise, one of the
        following categories describing the reason for the failure:
          * not_reproducible - When it was possible to run the reproduction script, but no issue was found
          * no_puppeteer_script - When the report has no Puppeteer reproduction script to verify against
          * no_intervention_possible - When the issue reproduces, but no UA override, content script, CSS
          or header change you tried fixed it (it likely needs a platform fix instead)
          * build_failed - When the artifact build would not complete
          * verification_failed - Set automatically when the reproduction script does not
          exit 0 against the build with the intervention; do not use it yourself
          * unsupported_android - When the report is specific to Android
          * unsupported_ios - When the report is specific to iOS
          * blocked_captcha - When access to the site was blocked because the page requires solving a captcha
          * blocked_geo - When access to the site was blocked based on location ("geoblocking")
          * blocked - When access to the site was blocked for some reason that couldn't be identified as a captcha or geoblocking
          * login - When reproducing the issue requires completing a login flow
          * down - When the site down or unavailable in a way that is unrelated to the issue report
          * headless - When there is an evidence that the issue isn't reproducible due to the headless environment
          * other - When the intervention could not be delivered for some other reason (briefly state the reason in the summary)
""",
        ),
    ]

    summary: Annotated[
        str,
        Field(
            description=(
                "Two or three sentences: what broke, what the intervention does, "
                "and what you verified. State conclusions, not the investigation."
            )
        ),
    ]


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
