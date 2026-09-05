"""Typed Phabricator domain models returned by the client."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PhabricatorDiff(BaseModel):
    """A Differential diff's identity and the commit it was built on.

    ``base_commit`` (Conduit's ``sourceControlBaseRevision``) is the commit to
    check the tree out at before applying the diff; it may be absent.

    ``author_name``/``author_email`` are whoever authored the commit the diff
    was built from, as recorded when it was submitted. Both are optional: a diff
    uploaded through the web UI carries no local commit to take them from, so
    callers must cope with having neither.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    base_commit: str | None = Field(default=None, alias="sourceControlBaseRevision")
    author_name: str | None = Field(default=None, alias="authorName")
    author_email: str | None = Field(default=None, alias="authorEmail")

    @property
    def author(self) -> str | None:
        """The author as git wants it, ``Name <email>``, or ``None`` if unknown."""
        if self.author_name and self.author_email:
            return f"{self.author_name} <{self.author_email}>"
        return None
