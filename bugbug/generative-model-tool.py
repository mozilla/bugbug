# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class ReviewData(ABC):
    @abstractmethod
    def get_patch_by_id(self, patch_id: int) -> "Patch":
        raise NotImplementedError


class PhabricatorReviewData(ReviewData):
    def get_patch_by_id(self, patch_id: int) -> "PhabricatorRevision":
        return self.get_by_revision_id(patch_id)

    def get_by_revision_id(self, revision_id: int) -> "PhabricatorRevision":
        raise NotImplementedError


class GithubReviewData(ReviewData):
    ...


class GitlabReviewData(ReviewData):
    ...


class Patch:
    ...


class PhabricatorRevision(Patch):
    ...


class GithubPullRequest(Patch):
    ...


class GitlabMergeRequest(Patch):
    ...


@dataclass
class InlineComment:
    filename: str
    start_line: int
    end_line: int
    comment: str
    on_added_code: bool


class GenerativeModelTool(ABC):
    @property
    @abstractmethod
    def version(self) -> str:
        ...

    @abstractmethod
    def run(self, *args, **kwargs) -> Any:
        ...


class CodeReviewTool(GenerativeModelTool):
    version = "0.0.1"

    def __init__(self, review_data: ReviewData, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.review_data = review_data

    def run(self, patch_id: int) -> list[InlineComment]:
        patch = self.review_data.get_patch_by_id(patch_id)  # noqa: F841

        raise NotImplementedError


class PhabricatorCodeReviewTool(CodeReviewTool):
    def __init__(self, *args, **kwargs) -> None:
        review_data = PhabricatorReviewData()

        super().__init__(review_data, *args, **kwargs)
