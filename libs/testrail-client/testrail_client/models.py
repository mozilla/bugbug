"""Typed TestRail domain models used by the client."""

from pydantic import BaseModel, ConfigDict, RootModel


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CaseType(_Model):
    """A test case type configured in TestRail (e.g. "Functional")."""

    id: int
    name: str


class Template(_Model):
    """A test case template configured in TestRail (e.g. "Test Case (Steps)")."""

    id: int
    name: str


class Status(_Model):
    """A test result status configured in TestRail (e.g. "Passed", "Failed")."""

    id: int
    name: str


class Suite(_Model):
    """A test suite created in TestRail."""

    id: int


class Section(_Model):
    """A section created inside a TestRail test suite."""

    id: int


class Case(_Model):
    """A test case created in TestRail."""

    id: int


class Run(_Model):
    """A test run created in TestRail."""

    id: int


class Result(_Model):
    """A test result recorded in TestRail."""

    id: int


class SeparatedStep(_Model):
    """One step of a test case, sent in TestRail's `custom_steps_separated` field.

    `content` is the action to perform, and `expected` is the expected outcome.
    """

    content: str
    expected: str = ""


class CaseResult(_Model):
    """A result to record for one test case in a run.

    Sent to TestRail's `add_results_for_cases` endpoint.
    """

    case_id: int
    status_id: int
    comment: str = ""


class _CaseTypesResponse(RootModel[list[CaseType]]):
    pass


class _TemplatesResponse(RootModel[list[Template]]):
    pass


class _StatusesResponse(RootModel[list[Status]]):
    pass


class _ResultsResponse(RootModel[list[Result]]):
    pass
