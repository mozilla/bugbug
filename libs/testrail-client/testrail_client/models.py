"""Typed TestRail domain models returned by the client."""

from pydantic import BaseModel, ConfigDict, RootModel


class TestRailModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class TestRailCaseType(TestRailModel):
    id: int
    name: str


class TestRailTemplate(TestRailModel):
    id: int
    name: str


class TestRailStatus(TestRailModel):
    id: int
    name: str


class TestRailSuite(TestRailModel):
    id: int


class TestRailSection(TestRailModel):
    id: int


class TestRailCase(TestRailModel):
    id: int


class TestRailRun(TestRailModel):
    id: int


class TestRailResult(TestRailModel):
    id: int


class TestRailStep(TestRailModel):
    """One step of a case using the "Test Case (Steps)" template."""

    content: str
    expected: str = ""


class TestRailResultInput(TestRailModel):
    """One result to record against a case in a run."""

    case_id: int
    status_id: int
    comment: str = ""


class TestRailCaseTypesResponse(RootModel[list[TestRailCaseType]]):
    pass


class TestRailTemplatesResponse(RootModel[list[TestRailTemplate]]):
    pass


class TestRailStatusesResponse(RootModel[list[TestRailStatus]]):
    pass


class TestRailResultsResponse(RootModel[list[TestRailResult]]):
    pass
