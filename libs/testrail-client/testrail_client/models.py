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


class TestRailCaseTypesResponse(RootModel[list[TestRailCaseType]]):
    pass


class TestRailTemplatesResponse(RootModel[list[TestRailTemplate]]):
    pass


class TestRailStatusesResponse(RootModel[list[TestRailStatus]]):
    pass


class TestRailResultsResponse(RootModel[list[TestRailResult]]):
    pass
