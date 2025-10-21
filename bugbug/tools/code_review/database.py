# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Database classes for code review comments and feedback."""

import enum
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from logging import getLogger
from typing import Iterable, Literal

from langchain_openai import OpenAIEmbeddings
from unidiff import Hunk, PatchSet

from bugbug.tools.core.data_types import InlineComment
from bugbug.tools.core.platforms.base import Patch
from bugbug.utils import get_secret
from bugbug.vectordb import PayloadScore, QueryFilter, VectorDB, VectorPoint

logger = getLogger(__name__)


class ReviewCommentsDB:
    NAV_PATTERN = re.compile(r"\{nav, [^}]+\}")
    WHITESPACE_PATTERN = re.compile(r"[\n\s]+")

    def __init__(self, vector_db: VectorDB) -> None:
        self.vector_db = vector_db
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-large", api_key=get_secret("OPENAI_API_KEY")
        )

    def clean_comment(self, comment: str):
        # We do not want to keep the LLM note in the comment, it is not useful
        # when using the comment as examples.
        llm_note_index = comment.find("> This comment was generated automatically ")
        if llm_note_index != -1:
            comment = comment[:llm_note_index]

        # TODO: use the nav info instead of removing it
        comment = self.NAV_PATTERN.sub("", comment)
        comment = self.WHITESPACE_PATTERN.sub(" ", comment)
        comment = comment.strip()

        return comment

    def add_comments_by_hunk(self, items: Iterable[tuple[Hunk, InlineComment]]):
        point_ids = set(self.vector_db.get_existing_ids())
        logger.info("Will skip %d comments that already exist", len(point_ids))

        def vector_points():
            for hunk, comment in items:
                if comment.id in point_ids:
                    continue

                str_hunk = str(hunk)
                vector = self.embeddings.embed_query(str_hunk)

                comment_data = asdict(comment)
                comment_data["content"] = self.clean_comment(comment.content)
                payload = {
                    "hunk": str_hunk,
                    "comment": comment_data,
                    "version": 2,
                }

                yield VectorPoint(id=comment.id, vector=vector, payload=payload)

        self.vector_db.insert(vector_points())

    def find_similar_hunk_comments(
        self,
        hunk: Hunk,
        generated: bool | None = None,
        created_before: datetime | None = None,
    ):
        return self.vector_db.search(
            self.embeddings.embed_query(str(hunk)),
            filter=(
                QueryFilter(
                    must_match=(
                        {"comment.is_generated": generated}
                        if generated is not None
                        else None
                    ),
                    must_range=(
                        {
                            "comment.date_created": {
                                "lt": created_before.timestamp(),
                            }
                        }
                        if created_before is not None
                        else None
                    ),
                )
            ),
        )

    def find_similar_patch_comments(
        self,
        patch: Patch,
        limit: int,
        generated: bool | None = None,
        created_before: datetime | None = None,
    ):
        assert limit > 0, "Limit must be greater than 0"

        patch_set = PatchSet.from_string(patch.raw_diff)

        # We want to avoid returning the same comment multiple times. Thus, if
        # a comment matches multiple hunks, we will only consider it once.
        max_score_per_comment: dict = {}
        for patched_file in patch_set:
            if not patched_file.is_modified_file:
                continue

            for hunk in patched_file:
                for result in self.find_similar_hunk_comments(
                    hunk, generated, created_before
                ):
                    if result is not None and (
                        result.id not in max_score_per_comment
                        or result.score > max_score_per_comment[result.id].score
                    ):
                        max_score_per_comment[result.id] = result

        return sorted(max_score_per_comment.values())[-limit:]


class EvaluationAction(enum.Enum):
    APPROVE = 1
    REJECT = 2
    IGNORE = 3


@dataclass
class SuggestionFeedback:
    id: int
    comment: str
    file_path: str
    action: Literal["APPROVE", "REJECT", "IGNORE"]
    user: str

    @staticmethod
    def from_payload_score(point: PayloadScore):
        return SuggestionFeedback(
            id=point.id,
            comment=point.payload["comment"],
            file_path=point.payload["file_path"],
            action=point.payload["action"],
            user=point.payload["user"],
        )


class SuggestionsFeedbackDB:
    def __init__(self, vector_db: VectorDB) -> None:
        self.vector_db = vector_db
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-large", api_key=get_secret("OPENAI_API_KEY")
        )

    def add_suggestions_feedback(self, suggestions: Iterable[SuggestionFeedback]):
        def vector_points():
            for suggestion in suggestions:
                vector = self.embeddings.embed_query(suggestion.comment)
                payload = {
                    "comment": suggestion.comment,
                    "file_path": suggestion.file_path,
                    "action": suggestion.action,
                    "user": suggestion.user,
                }

                yield VectorPoint(id=suggestion.id, vector=vector, payload=payload)

        self.vector_db.insert(vector_points())

    def find_similar_suggestions(self, comment: str):
        return (
            SuggestionFeedback.from_payload_score(point)
            for point in self.vector_db.search(self.embeddings.embed_query(comment))
        )

    def find_similar_rejected_suggestions(
        self, comment: str, limit: int, excluded_ids: Iterable[int] = ()
    ):
        return (
            SuggestionFeedback.from_payload_score(point)
            for point in self.vector_db.search(
                self.embeddings.embed_query(comment),
                filter=QueryFilter(
                    must_match={"action": "REJECT"},
                    must_not_has_id=list(excluded_ids),
                ),
                limit=limit,
            )
        )
