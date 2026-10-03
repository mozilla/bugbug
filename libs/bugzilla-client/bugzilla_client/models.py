"""Typed Bugzilla models: what the client returns, and what it sends.

For read models, only the fields callers rely on are declared. Everything else
Bugzilla returns is kept (``extra="allow"``), because ``include_fields`` lets a
caller ask for any field and the result should not silently lose it.
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Bug(BaseModel):
    """A bug. Every field is optional, as ``include_fields`` may omit any of them."""

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    summary: str | None = None
    status: str | None = None
    resolution: str | None = None
    product: str | None = None
    component: str | None = None
    keywords: list[str] | None = None
    whiteboard: str | None = None
    assigned_to: str | None = None
    creator: str | None = None
    creation_time: datetime | None = None
    last_change_time: datetime | None = None
    blocks: list[int] | None = None
    depends_on: list[int] | None = None


class Comment(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    bug_id: int
    count: int
    text: str
    creator: str
    creation_time: datetime
    is_private: bool = False
    attachment_id: int | None = None
    tags: list[str] = []


class Attachment(BaseModel):
    """An attachment. ``data`` (base64) is ``None`` when excluded from the query."""

    model_config = ConfigDict(extra="allow")

    id: int
    bug_id: int
    file_name: str
    summary: str
    content_type: str
    size: int | None = None
    is_obsolete: bool = False
    is_patch: bool = False
    is_private: bool = False
    creator: str | None = None
    creation_time: datetime | None = None
    data: str | None = None


class BugType(enum.StrEnum):
    defect = "defect"
    enhancement = "enhancement"
    task = "task"


class IdsChange(BaseModel):
    """Change a list of bug ids, e.g. ``blocks`` or ``depends_on``."""

    model_config = ConfigDict(extra="forbid")

    add: list[int] | None = None
    remove: list[int] | None = None
    set: list[int] | None = None


class KeywordsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    add: list[str] | None = None
    remove: list[str] | None = None
    set: list[str] | None = None


class AddRemove(BaseModel):
    """Change a list that can only be added to or removed from, e.g. ``cc``."""

    model_config = ConfigDict(extra="forbid")

    add: list[str] | None = None
    remove: list[str] | None = None


class NewComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str
    is_private: bool | None = None
    is_markdown: bool | None = None


class BugUpdate(BaseModel):
    """Field changes and/or a comment for ``PUT /bug/{id}``."""

    model_config = ConfigDict(extra="allow")

    comment: NewComment | None = None
    comment_tags: list[str] | None = None

    status: str | None = None
    resolution: str | None = None
    priority: str | None = None
    severity: str | None = None
    whiteboard: str | None = None
    assigned_to: str | None = None
    product: str | None = None
    component: str | None = None
    version: str | None = None
    keywords: KeywordsChange | None = None
    blocks: IdsChange | None = None
    depends_on: IdsChange | None = None
    cc: AddRemove | None = None
    see_also: AddRemove | None = None


class NewBug(BaseModel):
    """A bug to file with ``POST /bug``. ``description`` becomes comment 0."""

    model_config = ConfigDict(extra="allow")

    product: str
    component: str
    summary: str
    version: str
    description: str
    # BMO rejects a new bug without a type
    type: BugType

    priority: str | None = None
    severity: str | None = None
    assigned_to: str | None = None
    keywords: list[str] | None = None
    whiteboard: str | None = None
    blocks: list[int] | None = None
    depends_on: list[int] | None = None
    cc: list[str] | None = None
    see_also: list[str] | None = None
    groups: list[str] | None = None
    op_sys: str | None = None
    platform: str | None = None


class FieldChange(BaseModel):
    """What an update changed in one field, as Bugzilla reports it.

    Each side is a string, comma-separated when several values changed (e.g.
    keywords), and empty when nothing was removed or added.
    """

    added: str
    removed: str


class NewAttachment(BaseModel):
    """A file to attach with ``POST /bug/{id}/attachment``.

    ``data`` is the raw content; the client base64-encodes it when sending.
    """

    model_config = ConfigDict(extra="allow")

    data: bytes
    file_name: str
    summary: str
    content_type: str
    comment: str | None = None
    is_patch: bool | None = None
