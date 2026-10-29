"""Read the team file and the shared files.

A team directory (`teams/<name>/`) holds what differs between teams: `team.toml` (the
label, the components and the capture block a comment quotes) and `rules/`. The files in
`shared/` hold what every team uses: the Bugzilla keywords and fields
(`bugzilla.toml`) and how to tell Mozilla staff and QA apart (`people.toml`). All are
validated strictly when they are read, so a typo or a wrong type stops the agent at
import.
"""

import re
import tomllib
from pathlib import Path
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)


def _lower(values: list[str]) -> frozenset[str]:
    return frozenset(v.lower() for v in values)


Logins = Annotated[frozenset[StrictStr], BeforeValidator(_lower)]

# One Bugzilla login: no list, no display name, no spaces.
EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>.]+(?:\.[^@\s,;<>.]+)+")


def _email(value: str) -> str:
    value = value.strip().lower()
    if not EMAIL.fullmatch(value):
        raise ValueError(f"{value!r} is not one email address")
    return value


Email = Annotated[StrictStr, AfterValidator(_email)]
Text = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Component(_Strict):
    """One Bugzilla component a team triages."""

    product: StrictStr
    component: StrictStr
    # Where the component's code lives, for the prompt and for `docs.docs_for`.
    trees: tuple[StrictStr, ...]
    # Where its documentation is registered when that is not under `trees`.
    doc_trees: tuple[StrictStr, ...] = ()
    notes: StrictStr = ""

    @property
    def key(self) -> str:
        return f"{self.product} :: {self.component}"


class People(_Strict):
    employee_domain: Annotated[StrictStr, BeforeValidator(str.lower)]
    qa_name_markers: tuple[StrictStr, ...]
    # Lower-cased QA logins the name markers miss.
    qa_accounts: Logins = frozenset()


class CommentBlocks(_Strict):
    # The word that names the capture. A request that names it carries `capture_block`
    # verbatim, and no list item outside the block names it.
    capture_marker: Annotated[StrictStr, AfterValidator(str.lower)]
    capture_block: StrictStr
    # UI labels from the block. Two of them outside the block mean retyped steps.
    capture_labels: tuple[StrictStr, ...] = ()

    @model_validator(mode="after")
    def _labels_in_block(self) -> CommentBlocks:
        missing = [x for x in self.capture_labels if x not in self.capture_block]
        if missing:
            raise ValueError(f"capture_labels not in capture_block: {missing}")
        return self


def _twice(values: list[str]) -> list[str]:
    return sorted({v for v in values if values.count(v) > 1})


class NeedinfoExpert(_Strict):
    """A developer the agent may needinfo without a move, for the bug's area."""

    email: Email
    areas: tuple[Text, ...] = Field(min_length=1)
    # The condition the bug must meet before the agent asks, when one matters.
    when: Text | None = None

    @field_validator("areas")
    @classmethod
    def _each_area_once(cls, areas: tuple[str, ...]) -> tuple[str, ...]:
        if twice := _twice([a.casefold() for a in areas]):
            raise ValueError(f"areas listed twice: {', '.join(twice)}")
        return areas


# The value every team allows in addition to its own list, for a symptom no listed value
# fits.
OTHER_ISSUE_TYPE = "other"
_ISSUE_TYPE = re.compile(r"[a-z0-9-]+")


def _issue_type_value(value: str) -> str:
    if not _ISSUE_TYPE.fullmatch(value):
        raise ValueError(f"{value!r} is not a lower-case issue type of a-z, 0-9 and -")
    if value == OTHER_ISSUE_TYPE:
        raise ValueError(f"{OTHER_ISSUE_TYPE!r} is always allowed; do not list it")
    return value


class IssueType(_Strict):
    """One value of the bug's main symptom, for the run's `issue_type` finding."""

    value: Annotated[StrictStr, AfterValidator(_issue_type_value)]
    meaning: Text


class Team(_Strict):
    # How the prompt names the area, e.g. "Firefox Audio/Video".
    label: StrictStr
    comment: CommentBlocks
    components: tuple[Component, ...] = Field(min_length=1)
    needinfo_experts: tuple[NeedinfoExpert, ...] = ()
    # The developers to ask, one at most, when no expert's area matches and the code's
    # history names nobody.
    needinfo_fallback: tuple[Email, ...] = ()
    # The values the run's `issue_type` may take, besides `other`.
    issue_types: tuple[IssueType, ...] = ()

    @field_validator("needinfo_experts")
    @classmethod
    def _one_entry_per_expert(
        cls, experts: tuple[NeedinfoExpert, ...]
    ) -> tuple[NeedinfoExpert, ...]:
        if twice := _twice([e.email for e in experts]):
            raise ValueError(f"experts listed twice: {', '.join(twice)}")
        return experts

    @field_validator("needinfo_fallback")
    @classmethod
    def _each_fallback_once(cls, emails: tuple[str, ...]) -> tuple[str, ...]:
        if twice := _twice(list(emails)):
            raise ValueError(f"fallback emails listed twice: {', '.join(twice)}")
        return emails

    @field_validator("issue_types")
    @classmethod
    def _each_issue_type_once(
        cls, types: tuple[IssueType, ...]
    ) -> tuple[IssueType, ...]:
        if twice := _twice([t.value for t in types]):
            raise ValueError(f"issue types listed twice: {', '.join(twice)}")
        return types


class Keywords(_Strict):
    # A bug with this keyword is a meta bug and is skipped.
    meta: StrictStr
    # Keywords the agent may add.
    add: tuple[StrictStr, ...]


class Fields(_Strict):
    qa_whiteboard: StrictStr


class History(_Strict):
    # Fields whose changes give the agent nothing new to read.
    no_effect: tuple[Text, ...]
    no_effect_prefixes: tuple[Text, ...]

    def affects_analysis(self, field: str) -> bool:
        return field not in self.no_effect and not field.startswith(
            self.no_effect_prefixes
        )


class Whiteboard(_Strict):
    # A human adds this tag to keep the agent off a bug.
    skip: StrictStr
    # The tags the agent may add to an analyzed bug. `gfb_candidate` needs `easy_fix`.
    easy_fix: Text
    gfb_candidate: Text

    @model_validator(mode="after")
    def _distinct_tags(self) -> Whiteboard:
        if len({self.skip, self.easy_fix, self.gfb_candidate}) != 3:
            raise ValueError("the whiteboard tags must differ")
        return self

    @property
    def fix_tags(self) -> tuple[str, str]:
        return (self.easy_fix, self.gfb_candidate)


class NoTeam(_Strict):
    # The products a run with no team may triage; any other product is skipped.
    products: tuple[Text, ...] = Field(min_length=1)


class Terms(_Strict):
    keywords: Keywords
    fields: Fields
    history: History
    whiteboard: Whiteboard
    no_team: NoTeam
    meta_summary: Annotated[
        re.Pattern[str], BeforeValidator(lambda v: re.compile(v, re.IGNORECASE))
    ]


_TEAM_NAME = re.compile(r"[a-z][a-z0-9-]*")

# The rules each team writes, in `teams/<team>/rules/`.
TEAM_RULES = (
    "classify.md",
    "diagnostics.md",
    "comment.md",
    "severity-priority.md",
    "meta-bugs.md",
)


def team_dir(teams_dir: Path, name: str) -> Path:
    """The directory of team ``name``: `team.toml` and `rules/`.

    The name is a run input, so it must be a plain lower-case name of an existing team,
    never a path.
    """
    if not _TEAM_NAME.fullmatch(name):
        raise ValueError(f"team {name!r} is not a lower-case name")
    root = teams_dir / name
    if not (root / "team.toml").is_file():
        raise ValueError(f"no team {name!r} in {teams_dir}")
    missing = [n for n in TEAM_RULES if not (root / "rules" / n).is_file()]
    if missing:
        raise ValueError(f"team {name!r} lacks rules: {', '.join(missing)}")
    return root


def meta_bug_ids(text: str) -> frozenset[int]:
    """The bug ids in the table of a team's `meta-bugs.md`: `| alias | id | area |`.

    A data row whose second cell is not a bug id is an error, not a skipped row.
    """
    ids = set()
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.lstrip().startswith("|") or len(cells) < 2:
            continue
        if cells[1] == "Bug" or set(cells[1]) <= set("-: "):
            continue
        if not cells[1].isdigit():
            raise ValueError(f"meta-bugs.md row has no bug id: {line.strip()}")
        ids.add(int(cells[1]))
    return frozenset(ids)


def scope_for(team: Team | None, bug: dict) -> tuple[Component, ...]:
    """The components a run triages: the team's, or with no team the bug's own.

    With no team, a bug with no product or no component has no scope.
    """
    if team is not None:
        return team.components
    product, component = bug.get("product"), bug.get("component")
    if not product or not component:
        return ()
    own = Component(
        product=str(product),
        component=str(component),
        trees=(),
        notes="The bug's own component. This run has no team.",
    )
    return (own,)


def load_team(path: Path) -> Team:
    return Team.model_validate(tomllib.loads(path.read_text()))


def load_terms(path: Path) -> Terms:
    return Terms.model_validate(tomllib.loads(path.read_text()))


def load_people(path: Path) -> People:
    return People.model_validate(tomllib.loads(path.read_text()))
