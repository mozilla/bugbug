"""What step 1 returns: an impact rating, and the comment the calculator wrote.

The shape here is deliberately thin, and the omissions are the design. See
``~/projects/docs/triage-agent-mode-contract.md``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ImpactRating = Literal["low", "medium", "high", "none"]

Severity = Literal["S1", "S2", "S3", "S4"]


class ProfileFinding(BaseModel):
    """What the skill's profile step found, when it ran.

    Present only when agent mode analysed a profile, which the caller
    asks for with ``analyse_profile``. ``component`` is therefore a real judgement here
    (the skill's JS-frame attribution check governs it), not the structural
    constant it was when the profile step was always skipped.
    """

    url: str | None = Field(
        default=None, description="The profile URL analysed; null for an attachment."
    )
    attachment: int | None = Field(
        default=None, description="Attachment id, when a profile file was analysed."
    )
    analysed: bool
    not_analysed_reason: str | None = None
    changed_attributes: dict[str, str | bool | list[str]] = Field(
        default_factory=dict,
        description="Calculator inputs the profile changed, by argument name.",
    )
    component: str | None = Field(
        default=None,
        description="`Product :: Component` the bug should move to; null to keep it.",
    )
    summary: str | None = Field(
        default=None, description="Two or three sentences on the hot path."
    )


class MemoryGroup(BaseModel):
    """The largest `explicit/` group in an about:memory report."""

    process: str
    path: str
    mb: float


class MemoryReportFinding(BaseModel):
    """What the skill's Step 3b found in an attached about:memory report."""

    attachment: int
    dominant: MemoryGroup | None = None
    component: str | None = Field(
        default=None,
        description="`Product :: Component` the bug should move to; null to keep it.",
    )
    summary: str | None = Field(
        default=None, description="One or two sentences on what the report implies."
    )
    changed_attributes: dict[str, str | bool | list[str]] = Field(
        default_factory=dict,
        description="Calculator inputs the memory report changed, by argument name.",
    )


class ComponentDecision(BaseModel):
    """The skill's final component recommendation, from whichever evidence decided it.

    ``profile.component`` and ``memory_report.component`` stay as what each piece
    of evidence points to; this is the one decision, with the profile winning a
    disagreement with the memory report.
    """

    action: Literal["keep", "move"]
    name: str = Field(
        description="Current `Product :: Component` for keep, the target for move."
    )
    basis: Literal["bug", "profile", "memory_report"]
    verified: bool = Field(
        description="False only when the profile attribution is unverified (then keep)."
    )
    justification: str | None = None


class RelatedBug(BaseModel):
    """A same-mechanism related bug the skill took calculator inputs from.

    Carried so the posted comment can name it: an input set from another bug is
    otherwise invisible to a reader of this one.
    """

    bug: int
    settled: list[str] = Field(
        default_factory=list,
        description="Calculator inputs it settled, by argument name.",
    )


class TriageResult(BaseModel):
    """The calculator's verdict on a bug, plus the severity it does not compute.

    ``component`` is the skill's single recommendation. It carries the evidence
    that decided it (``basis``) and whether the profile attribution was verified,
    so a "keep" from bug text alone reads as what it is rather than as a
    profile-backed judgement.
    """

    impact: ImpactRating = Field(
        description=(
            "The calculator's Performance Impact rating. This is the field the "
            "Bugzilla `cf_performance_impact` flag is set from."
        )
    )
    score: float = Field(
        description=(
            "The calculator's numeric score behind the rating. Kept because two "
            "bugs rated 'medium' are not equally medium, and the number is what "
            "makes a re-triage argument concrete."
        )
    )
    keywords: list[str] = Field(
        default_factory=list,
        description=(
            "The `perf:*` keywords the calculator derived, plus 'reproducible' "
            "when it applies. These are what a sweep counts, so they are stored "
            "as a list rather than folded into the comment text."
        ),
    )
    severity: Severity | None = Field(
        default=None,
        description=(
            "S1-S4, which the calculator does not compute -- it comes from the "
            "skill's own judgement. Null when the run could not justify one; a "
            "guessed severity is worse than none because a human will act on it."
        ),
    )
    comment: str = Field(
        description=(
            "The Bugzilla comment text, exactly as the calculator rendered it. "
            "NOT re-wrapped or summarised by us: it already contains the "
            "calculator URL, the rating, and the selections that produced it, "
            "which is what makes the rating auditable and re-triageable."
        )
    )
    calculator_url: str | None = Field(
        default=None,
        description=(
            "The permalink that reproduces this rating in the web calculator. "
            "The single most useful thing in the output for a human who "
            "disagrees: it opens the form with every selection already made."
        ),
    )
    selections: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "The raw flag selections the calculator scored, e.g. "
            "{'platforms': ['platform-windows'], 'browser': ['browser-jank']}. "
            "Kept because this is what perf-triage-validator compares against a "
            "human adjudication -- the rating alone cannot show WHERE a "
            "disagreement is."
        ),
    )
    unestablished: list[str] = Field(
        default_factory=list,
        description=(
            "Calculator inputs the bug did not establish, as perf_triage.py "
            "argument names. What the stability check varies."
        ),
    )
    related: list[RelatedBug] = Field(
        default_factory=list,
        description="Related bugs the skill took calculator inputs from.",
    )
    profile: ProfileFinding | None = Field(
        default=None,
        description="The profile step's finding; null when it did not run.",
    )
    component: ComponentDecision | None = Field(
        default=None,
        description="The skill's final component recommendation; null if it gave none.",
    )
    memory_report: MemoryReportFinding | None = Field(
        default=None,
        description="Step 3b's finding on an attached memory report; null if none.",
    )
    failure_reason: str | None = Field(
        default=None,
        description=(
            "Set when triage could not run at all -- the calculator was not "
            "found, the bug could not be fetched, the skill returned nothing "
            "parseable. A sweep checks this field rather than grepping logs, "
            "because the agent narrates its own reasoning into prose and a log "
            "grep once aborted a healthy sweep on exactly that."
        ),
    )

    @property
    def ok(self) -> bool:
        """Whether this result carries a usable rating."""
        return self.failure_reason is None
