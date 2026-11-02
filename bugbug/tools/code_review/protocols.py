from typing import Protocol

from bugbug.tools.code_review.data_types import GeneratedReviewComment
from bugbug.tools.core.platforms.base import Patch


class PatchSummarizer(Protocol):
    def run(self, patch: Patch) -> str: ...


class SuggestionFilterer(Protocol):
    def run(
        self, suggestions: list[GeneratedReviewComment]
    ) -> list[GeneratedReviewComment]: ...
