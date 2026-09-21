"""Typed TestRail domain models returned by the client."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


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
