"""Typed Phabricator domain models returned by the client."""

from __future__ import annotations

from typing import Annotated

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
    base_commit: Annotated[str | None, Field(alias="sourceControlBaseRevision")] = None
    author_name: Annotated[str | None, Field(alias="authorName")] = None
    author_email: Annotated[str | None, Field(alias="authorEmail")] = None
    properties: dict = Field(default_factory=dict)

    @property
    def author(self) -> str | None:
        """The author as git wants it, ``Name <email>``, or ``None`` if unknown."""
        if self.author_name and self.author_email:
            return f"{self.author_name} <{self.author_email}>"
        return None

    @property
    def first_public_parent(self) -> str | None:
        """The first landed parent recorded by mozphab, if present."""
        local_commits = self.properties.get("local:commits")
        if not local_commits:
            log.warning("The diff has no commit metadata")
            return None
        for commit in local_commits.values():
            first_public_parent = commit.get("firstPublicParent")
            if first_public_parent:
                return first_public_parent
        return None
