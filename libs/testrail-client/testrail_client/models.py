"""Typed TestRail domain models returned by the client."""

from pydantic import BaseModel, ConfigDict, RootModel


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CaseType(_Model):
    id: int
    name: str


class Template(_Model):
    id: int
    name: str


class Status(_Model):
    id: int
    name: str


class Suite(_Model):
    id: int


class Section(_Model):
    id: int


class Case(_Model):
    id: int


class Run(_Model):
    id: int


class Result(_Model):
    id: int


class _CaseTypesResponse(RootModel[list[CaseType]]):
    pass


class _TemplatesResponse(RootModel[list[Template]]):
    pass


class _StatusesResponse(RootModel[list[Status]]):
    pass


class _ResultsResponse(RootModel[list[Result]]):
    pass
