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


class RevisionPatch(BaseModel):
    """One revision's diff, as raw unified-diff text.

    ``base_commit`` is what the revision itself recorded as the commit it was
    built on, unexpanded and possibly unfetchable (see :class:`PhabricatorDiff`).
    Keep it so an updated diff can declare the base the revision already had,
    rather than whatever local commit the tree was rebuilt from.
    """

    revision_id: int
    diff_id: int
    base_commit: str
    raw_diff: str


class PatchStack(BaseModel):
    """A fetchable base commit plus the patches that rebuild a revision's tree.

    ``patches`` is ordered bottom-first: apply them in order onto
    ``base_commit`` and the last one is the requested revision. A revision that
    is not stacked (or whose ancestors have all landed) yields a single patch.
    """

    base_commit: str
    patches: list[RevisionPatch]
