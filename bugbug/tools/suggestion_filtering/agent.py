# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Suggestion filtering tool implementation."""

from typing import Iterable, Optional

from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain.chat_models import BaseChatModel
from langchain.messages import HumanMessage
from pydantic import BaseModel, Field

from bugbug.tools.base import GenerativeModelTool
from bugbug.tools.code_review.agent import GeneratedReviewComment
from bugbug.tools.code_review.database import SuggestionsFeedbackDB
from bugbug.tools.suggestion_filtering.prompts import (
    DEFAULT_REJECTED_EXAMPLES,
    PROMPT_TEMPLATE_FILTERING_ANALYSIS,
)


class FilteredComments(BaseModel):
    """The response from the filtering agent."""

    comment_indices: list[int] = Field(
        description="A list of indices of the comments that were kept after filtering"
    )


class SuggestionFilteringTool(GenerativeModelTool):
    """Tool for filtering generated review comments.

    Filters out low-quality suggestions using an LLM agent with
    optional dynamic rejected examples from a feedback database.
    """

    def __init__(
        self,
        llm: BaseChatModel,
        target_software: str = "Mozilla Firefox",
        suggestions_feedback_db: Optional[SuggestionsFeedbackDB] = None,
    ) -> None:
        self.target_software = target_software
        self.suggestions_feedback_db = suggestions_feedback_db
        self.agent = create_agent(
            llm,
            response_format=ProviderStrategy(FilteredComments),
        )

    def get_indices_of_retained_comments(
        self, suggestions: list[GeneratedReviewComment]
    ) -> list[int]:
        """Get indices of comments to keep after filtering.

        Args:
            suggestions: List of generated review comments to filter.

        Returns:
            List of indices of comments to keep.
        """
        if not suggestions:
            return []

        rejected_examples = self._get_rejected_examples(suggestions)

        formatted_comments = "\n".join(
            f"Index {i}: {comment.model_dump()}"
            for i, comment in enumerate(suggestions)
        )

        prompt = PROMPT_TEMPLATE_FILTERING_ANALYSIS.format(
            target_code_consistency=self.target_software,
            comments=formatted_comments,
            rejected_examples=rejected_examples,
        )

        result = self.agent.invoke({"messages": [HumanMessage(prompt)]})

        return result["structured_response"].comment_indices

    def run(
        self, suggestions: list[GeneratedReviewComment]
    ) -> list[GeneratedReviewComment]:
        """Filter the given suggestions and return filtered comments.

        Args:
            suggestions: List of generated review comments to filter.

        Returns:
            List of filtered GeneratedReviewComment objects.
        """
        return [
            suggestions[i] for i in self.get_indices_of_retained_comments(suggestions)
        ]

    def _get_rejected_examples(self, suggestions: list[GeneratedReviewComment]) -> str:
        """Get rejected examples for filtering.

        Uses dynamic examples from feedback database if available,
        otherwise falls back to default static examples.
        """
        if not self.suggestions_feedback_db:
            return DEFAULT_REJECTED_EXAMPLES

        rejected_comments = list(self._get_similar_rejected_comments(suggestions))
        if not rejected_comments:
            return DEFAULT_REJECTED_EXAMPLES

        return "\n    - ".join(rejected_comments)

    def _get_similar_rejected_comments(
        self, suggestions: list[GeneratedReviewComment]
    ) -> Iterable[str]:
        """Find similar rejected comments from the feedback database."""
        if not self.suggestions_feedback_db:
            raise Exception("Suggestions feedback database is not available")

        num_examples_per_suggestion = 10 // len(suggestions) or 1
        seen_ids: set[int] = set()

        for suggestion in suggestions:
            similar_rejected_suggestions = (
                self.suggestions_feedback_db.find_similar_rejected_suggestions(
                    suggestion.comment,
                    limit=num_examples_per_suggestion,
                    excluded_ids=seen_ids,
                )
            )
            for rejected_suggestion in similar_rejected_suggestions:
                seen_ids.add(rejected_suggestion.id)
                yield rejected_suggestion.comment

    @classmethod
    def create(cls, **kwargs):
        """Factory method to instantiate the tool with default dependencies.

        This method takes the same parameters as the constructor, but all
        parameters are optional. If a parameter is not provided, a default
        component will be created and used.
        """
        if "suggestions_feedback_db" not in kwargs:
            from bugbug.vectordb import QdrantVectorDB

            kwargs["suggestions_feedback_db"] = SuggestionsFeedbackDB(
                QdrantVectorDB("suggestions_feedback")
            )

        if "llm" not in kwargs:
            from bugbug.tools.core.llms import create_anthropic_llm

            kwargs["llm"] = create_anthropic_llm()

        return cls(**kwargs)
