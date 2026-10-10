"""Structured result reporting for the autowebcompat-intervention agent."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

# Name of the in-process MCP server that exposes `submit_result`.
RESULT_SERVER_NAME = "autowebcompat-intervention"


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

    affected_platforms: Annotated[
        list[
            Literal["windows"]
            | Literal["mac"]
            | Literal["linux"]
            | Literal["android"]
            | Literal["ios"]
        ],
        Field(
            min_length=1,
            description=(
                "The platforms the issue affects, in the terms of the bug's "
                "`cf_user_story` `platform` field. An issue with no evidence of being "
                "OS-specific affects all three desktop OSes. Use `ios` only, on its "
                "own, for an issue limited to Firefox for iOS."
            ),
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

    failure_reason: Annotated[
        (
            Literal["not_reproducible"]
            | Literal["no_puppeteer_script"]
            | Literal["no_intervention_possible"]
            | Literal["build_failed"]
            | Literal["verification_failed"]
            | Literal["unexpected_changes"]
            | Literal["unsupported_android"]
            | Literal["unsupported_ios"]
            | Literal["unsupported_desktop_os"]
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
          * unexpected_changes - Set automatically when the patch touches files outside
          browser/extensions/webcompat/ or testing/webcompat/; do not use it yourself
          * unsupported_android - When the report is specific to Android
          * unsupported_ios - When the report is specific to iOS
          * unsupported_desktop_os - When the report is specific to a desktop OS that isn't
          available in the current environment (e.g. macOS)
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
