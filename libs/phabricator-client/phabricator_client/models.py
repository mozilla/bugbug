"""Typed Phabricator domain models returned by the client."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PhabricatorDiff(BaseModel):
    """A Differential diff's identity and the commit it was built on.

    ``base_commit`` (Conduit's ``sourceControlBaseRevision``) is the commit to
    check the tree out at before applying the diff; it may be absent.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    base_commit: str | None = Field(default=None, alias="sourceControlBaseRevision")
