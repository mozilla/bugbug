"""Structured result reporting for the autowebcompat-diagnosis agent."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

RESULT_SERVER_NAME = "autowebcompat-diagnosis"


class DiagnosisPlanResult(BaseModel):
    """What the later tasks need, gathered before any browser is installed."""

    firefox_channel: Annotated[
        Literal["nightly"] | Literal["stable"] | Literal["esr"],
        Field(
            description=("The Firefox channel to diagnose on."),
        ),
    ]

    channel_rationale: Annotated[
        str,
        Field(
            description=(
                "One or two sentences on why you chose that channel, citing what "
                "you based it on (the `autowebcompat-repro-channels` marker, the "
                "report text, or the absence of both)."
            ),
        ),
    ]

    url: Annotated[
        str,
        Field(
            description="The URL of the page the issue was reported on.",
        ),
    ]

    steps: Annotated[
        str,
        Field(
            description=(
                "The steps to reproduce the issue, as a single numbered list (1., "
                "2., 3., ... one step per line), taken from the report and written "
                "so another agent could follow them with no extra context. Each "
                "step must be self-contained: whenever a step involves an input the "
                "report did not provide, state its exact origin. Always fill this "
                "in, even when a reproduction script is attached — the script may "
                "turn out not to work."
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


class ReproScriptResult(BaseModel):
    """Verdict from the script task: can the issue still be reproduced?"""

    reproduced: Annotated[
        bool,
        Field(
            description=(
                "true if you confirmed the reported issue still reproduces in "
                "Firefox but not in Chrome, whether via a Puppeteer script or by "
                "driving the site with the DevTools tools. false if you could not "
                "reproduce it."
            ),
        ),
    ]

    failure_reason: Annotated[
        (
            Literal["not_reproducible"]
            | Literal["not_firefox_specific"]
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
            description="""Null if the issue reproduced. Otherwise the category
        describing why it did not:
          * not_reproducible - all the steps ran, but the reported issue did not occur
          * not_firefox_specific - the reported behavior reproduces in both Firefox
          and Chrome
          * blocked_captcha - the site required solving a captcha
          * blocked_geo - the site blocked access based on location
          * blocked - access was blocked for a reason that isn't a captcha or geoblocking
          * login - reproducing requires completing a login flow
          * down - the site is down or unavailable, unrelated to the report
          * headless - there is evidence the issue isn't reproducible due to the
          headless environment
          * other - some other reason (give details in the summary)
""",
        ),
    ]

    summary: Annotated[
        str,
        Field(
            description=(
                "A concise account of what you did and what you observed in each "
                "browser, including why reproduction failed if it did."
            ),
        ),
    ]

    script_path: Annotated[
        Path | None,
        Field(
            description=(
                "The file path of the Puppeteer script that demonstrates the "
                "difference — Firefox exits 1 and Chrome exits 0. Use the exact "
                "path you were given to write to (do NOT paste the script source). "
                "Null if no script validated; that is acceptable and does not by "
                "itself mean the issue failed to reproduce."
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


def check_written_file(path: Path | None) -> Path | None:
    if path is None:
        return None

    if not path.exists():
        raise ValueError(f"{path} doesn't exist")
    if not path.read_text().strip():
        raise ValueError(f"{path} is empty")
    return path


BUGZILLA_COMMENT_FORMAT = (
    " This field is posted, together with the other one, as a Bugzilla comment "
    "rendered as Markdown, so format it accordingly: no headings, and a blank "
    "line between paragraphs. Keep both fields together readable in under a "
    "minute."
)


class DiagnosisText(BaseModel):
    """The contents of the diagnosis JSON file the agent writes."""

    root_cause: Annotated[
        str,
        Field(
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
                + BUGZILLA_COMMENT_FORMAT
            ),
        ),
    ]

    evidence: Annotated[
        str,
        Field(
            description=(
                "The concrete observations supporting the hypothesis: console "
                "errors, network requests, DOM or computed-style measurements, "
                "feature-detection results, and what the reduced testcase showed in "
                "each browser. Be brief, this will be read by a busy engineer. "
                "Cite what you actually observed, not what you expect."
                + BUGZILLA_COMMENT_FORMAT
            ),
        ),
    ]

    @field_validator("root_cause", "evidence", mode="after")
    @classmethod
    def validate_text(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("must not be empty")
        return text


class DiagnosisResult(BaseModel):
    """The agent's root-cause account of why Firefox differs from Chrome.

    The long text fields are written to a JSON file rather than passed as tool
    arguments, because long string arguments make the model leak tool-call
    markup into them.
    """

    diagnosis_path: Annotated[
        Path,
        Field(
            description=(
                "The file path of the JSON file containing your `root_cause` and "
                "`evidence`. Use the exact path you were given to write to (do NOT "
                "paste the JSON)."
            ),
        ),
    ]

    testcase_path: Annotated[
        Path | None,
        Field(
            description=(
                "The file path of the reduced HTML testcase you wrote. Set this only "
                "if you loaded it in both browsers and confirmed it shows the same "
                "difference as the real site. Use the exact path you were given to "
                "write to (do NOT paste the HTML source). Null if you could not "
                "produce a reduced testcase that reproduces the difference."
            ),
        ),
    ]

    @field_validator("diagnosis_path", "testcase_path", mode="after")
    @classmethod
    def validate_paths(cls, path: Path | None) -> Path | None:
        return check_written_file(path)

    @field_validator("diagnosis_path", mode="after")
    @classmethod
    def validate_diagnosis(cls, path: Path) -> Path:
        try:
            DiagnosisText.model_validate_json(path.read_text())
        except ValidationError as exc:
            raise ValueError(f"{path} is not a valid diagnosis: {exc}") from exc
        return path

    @property
    def diagnosis(self) -> DiagnosisText:
        return DiagnosisText.model_validate_json(self.diagnosis_path.read_text())
