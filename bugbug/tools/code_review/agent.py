# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Code review agent implementation."""

import json
import os
from datetime import datetime
from logging import getLogger
from typing import Optional

from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain.chat_models import BaseChatModel
from langchain.messages import HumanMessage
from langgraph.errors import GraphRecursionError
from unidiff import PatchSet

from bugbug.code_search.function_search import FunctionSearch
from bugbug.tools.base import GenerativeModelTool
from bugbug.tools.code_review.data_types import (
    AgentResponse,
    CodeReviewToolResponse,
    GeneratedReviewComment,
)
from bugbug.tools.code_review.database import ReviewCommentsDB
from bugbug.tools.code_review.langchain_tools import (
    CodeReviewContext,
    create_find_function_definition_tool,
    expand_context,
)
from bugbug.tools.code_review.prompts import (
    FIRST_MESSAGE_TEMPLATE,
    STATIC_COMMENT_EXAMPLES,
    SYSTEM_PROMPT_TEMPLATE,
    TEMPLATE_COMMENT_EXAMPLE,
    TEMPLATE_PATCH_FROM_HUNK,
)
from bugbug.tools.code_review.protocols import (
    PatchSummarizer,
    SuggestionFilterer,
)
from bugbug.tools.code_review.utils import (
    convert_generated_comments_to_inline,
    format_patch_set,
)
from bugbug.tools.core.exceptions import LargeDiffError, ModelResultError
from bugbug.tools.core.llms import get_tokenizer
from bugbug.tools.core.platforms.base import Patch

logger = getLogger(__name__)


class CodeReviewTool(GenerativeModelTool):
    version = 2

    def __init__(
        self,
        llm: BaseChatModel,
        patch_summarizer: PatchSummarizer,
        suggestion_filterer: SuggestionFilterer,
        function_search: Optional[FunctionSearch] = None,
        review_comments_db: Optional["ReviewCommentsDB"] = None,
        show_patch_example: bool = False,
        verbose: bool = True,
        target_software: str = "Mozilla Firefox",
    ) -> None:
        super().__init__()

        self.target_software = target_software

        self._tokenizer = get_tokenizer(
            llm.model_name if hasattr(llm, "model_name") else ""
        )
        self.is_experiment_env = os.getenv("EXPERIMENT_ENV", "no").lower() in (
            "1",
            "yes",
            "true",
        )
        if self.is_experiment_env:
            print(
                "---------------------- WARNING ---------------------\n"
                "You are using the experiment environment.\n"
                "This environment is not intended for production use.\n"
                "----------------------------------------------------"
            )

        self.patch_summarizer = patch_summarizer
        self.suggestion_filterer = suggestion_filterer

        tools = [expand_context]
        if function_search:
            tools.append(create_find_function_definition_tool(function_search))

        self._agent_model = llm

        self.agent = create_agent(
            llm,
            tools,
            system_prompt=SYSTEM_PROMPT_TEMPLATE.format(
                target_software=self.target_software,
            ),
            response_format=ProviderStrategy(AgentResponse),
        )

        self.review_comments_db = review_comments_db

        self.show_patch_example = show_patch_example

        self.verbose = verbose

    @property
    def _agent_model_name(self) -> str:
        model = self._agent_model

        if hasattr(model, "model_name"):
            return model.model_name

        if hasattr(model, "model"):
            return model.model

        return str(model)

    @classmethod
    def create(cls, **kwargs):
        """Factory method to instantiate the tool with default dependencies.

        This method takes the same parameters as the constructor, but all
        parameters are optional. If a parameter is not provided, a default
        component will be created and used.
        """
        if "function_search" not in kwargs:
            from bugbug.code_search.searchfox_api import FunctionSearchSearchfoxAPI

            kwargs["function_search"] = FunctionSearchSearchfoxAPI()

        if "review_comments_db" not in kwargs:
            from bugbug.tools.code_review.database import ReviewCommentsDB
            from bugbug.vectordb import QdrantVectorDB

            kwargs["review_comments_db"] = ReviewCommentsDB(
                QdrantVectorDB("diff_comments")
            )

        if "llm" not in kwargs:
            from bugbug.tools.core.llms import create_anthropic_llm

            kwargs["llm"] = create_anthropic_llm(
                model_name="claude-opus-4-5-20251101",
                max_tokens=40_000,
                temperature=None,
                thinking={"type": "enabled", "budget_tokens": 10_000},
            )

        if "patch_summarizer" not in kwargs:
            from bugbug.tools.patch_summarization.agent import PatchSummarizationTool

            kwargs["patch_summarizer"] = PatchSummarizationTool.create()

        if "suggestion_filterer" not in kwargs:
            from bugbug.tools.suggestion_filtering.agent import SuggestionFilteringTool

            kwargs["suggestion_filterer"] = SuggestionFilteringTool.create()

        return cls(**kwargs)

    def count_tokens(self, text):
        return len(self._tokenizer.encode(text))

    def generate_initial_prompt(self, patch: Patch, patch_summary: str) -> str:
        created_before = patch.date_created if self.is_experiment_env else None

        return FIRST_MESSAGE_TEMPLATE.format(
            patch=format_patch_set(patch.patch_set),
            patch_summarization=patch_summary,
            comment_examples=self._get_comment_examples(patch, created_before),
            approved_examples=self._get_generated_examples(patch, created_before),
        )

    async def generate_review_comments(
        self, patch: Patch, patch_summary: str
    ) -> list[GeneratedReviewComment]:
        try:
            async for chunk in self.agent.astream(
                {
                    "messages": [
                        HumanMessage(
                            self.generate_initial_prompt(patch, patch_summary)
                        ),
                    ]
                },
                context=CodeReviewContext(patch=patch),
                stream_mode="values",
                config={"recursion_limit": 50},
            ):
                result = chunk
        except GraphRecursionError as e:
            raise ModelResultError("The model could not complete the review") from e

        return result["structured_response"].comments

    async def run(self, patch: Patch) -> CodeReviewToolResponse:
        if self.count_tokens(patch.raw_diff) > 21000:
            raise LargeDiffError("The diff is too large")

        patch_summary = self.patch_summarizer.run(patch)

        unfiltered_suggestions = await self.generate_review_comments(
            patch, patch_summary
        )
        if not unfiltered_suggestions:
            logger.info("No suggestions were generated")

        filtered_suggestions = self.suggestion_filterer.run(unfiltered_suggestions)

        inline_comments = list(
            convert_generated_comments_to_inline(filtered_suggestions, patch.patch_set)
        )

        return CodeReviewToolResponse(
            review_comments=inline_comments,
            patch_summary=patch_summary,
            details={
                "model": self._agent_model_name,
                "num_unfiltered_suggestions": len(unfiltered_suggestions),
            },
        )

    def _get_generated_examples(self, patch, created_before: datetime | None = None):
        """Get examples of comments that were generated by an LLM.

        Since the comments are posted, it means that they were approved by a
        reviewer. Thus, we can use them as examples of good comments.
        """
        if not self.review_comments_db:
            return ""

        comment_examples = [
            result.payload["comment"]["content"]
            for result in self.review_comments_db.find_similar_patch_comments(
                patch, limit=5, generated=True, created_before=created_before
            )
        ]
        if not comment_examples:
            return ""

        template = """
**Examples of comments that you suggested on other patches and developers found useful**:

- {comment_examples}
"""

        return template.format(comment_examples="\n    - ".join(comment_examples))

    def _get_comment_examples(self, patch, created_before: datetime | None = None):
        comment_examples = []

        if self.review_comments_db:
            comment_examples = [
                result.payload
                for result in self.review_comments_db.find_similar_patch_comments(
                    patch, limit=10, generated=False, created_before=created_before
                )
            ]

        if not comment_examples:
            comment_examples = STATIC_COMMENT_EXAMPLES
        else:
            for example in comment_examples:
                example["comment"]["explanation"] = "THE JUSTIFICATION GOES HERE"

        def format_comment(comment):
            # TODO: change the schema that we expect the model to return so we
            # can remove this function.
            return {
                "file": comment["filename"],
                "code_line": comment["start_line"],
                "comment": comment["content"],
            }

        def generate_formatted_patch_from_raw_hunk(raw_hunk, filename):
            patch = TEMPLATE_PATCH_FROM_HUNK.format(
                filename=filename, raw_hunk=raw_hunk
            )
            patch_set = PatchSet.from_string(patch)
            return format_patch_set(patch_set)

        if not self.show_patch_example:
            return json.dumps(
                [format_comment(example["comment"]) for example in comment_examples],
                indent=2,
            )

        return "\n\n".join(
            TEMPLATE_COMMENT_EXAMPLE.format(
                example_number=num + 1,
                patch=generate_formatted_patch_from_raw_hunk(
                    example["raw_hunk"], example["comment"]["filename"]
                ),
                comments=json.dumps(
                    [format_comment(example["comment"])],
                    indent=2,
                ),
            )
            for num, example in enumerate(comment_examples)
        )
