# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Swarm platform implementation for code review."""

from datetime import datetime
from functools import cached_property
from typing import Iterable

from bugbug.tools.core.data_types import InlineComment, ReviewRequest
from bugbug.tools.core.platforms.base import Patch, ReviewData
from bugbug.utils import get_secret


class SwarmPatch(Patch):
    def __init__(self, patch_id: str, auth: dict) -> None:
        self._patch_id = patch_id
        self.auth = auth
        self.rev_id = int(patch_id)

    @property
    def patch_id(self) -> str:
        return self._patch_id

    @cached_property
    def _revision_metadata(self) -> dict:
        import swarm

        revisions = swarm.get(self.auth, rev_ids=[self.rev_id])
        assert len(revisions) == 1

        return revisions[0]

    @property
    def raw_diff(self) -> str:
        revision = self._revision_metadata

        return revision["fields"]["diff"]

    @cached_property
    def base_commit_hash(self) -> str:
        raise NotImplementedError

    @property
    def date_created(self) -> datetime:
        revision = self._revision_metadata

        return datetime.fromtimestamp(revision["fields"]["created"])

    @cached_property
    def patch_title(self) -> str:
        raise NotImplementedError

    @cached_property
    def bug_title(self) -> str:
        raise NotImplementedError

    @property
    def patch_url(self) -> str:
        raise NotImplementedError

    def get_old_file(self, file_path: str) -> str:
        raise NotImplementedError


class SwarmReviewData(ReviewData):
    def __init__(self):
        self.auth = {
            "user": get_secret("SWARM_USER"),
            "password": get_secret("SWARM_PASS"),
            "port": get_secret("SWARM_PORT"),
            "instance": get_secret("SWARM_INSTANCE"),
        }

    def get_review_request_by_id(self, revision_id: int) -> ReviewRequest:
        return ReviewRequest(revision_id)

    def get_patch_by_id(self, patch_id: str) -> Patch:
        return SwarmPatch(patch_id, self.auth)

    def get_all_inline_comments(
        self, comment_filter
    ) -> Iterable[tuple[int, list[InlineComment]]]:
        # Todo
        raise NotImplementedError
