# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Code review agent implementation."""

import json
import os
from datetime import datetime
from logging import getLogger
from typing import Iterable, Literal, Optional

from langchain.agents import create_agent
from langchain.chat_models import BaseChatModel
from langchain.messages import HumanMessage
from langchain_classic.chains import LLMChain
from langchain_classic.prompts import PromptTemplate
from langgraph.errors import GraphRecursionError
from unidiff import PatchSet

from bugbug.code_search.function_search import FunctionSearch
from bugbug.tools.base import GenerativeModelTool
from bugbug.tools.code_review.database import ReviewCommentsDB, SuggestionsFeedbackDB
from bugbug.tools.code_review.langchain_tools import (
    CodeReviewContext,
    create_find_function_definition_tool,
    expand_context,
)
from bugbug.tools.code_review.prompts import (
    DEFAULT_REJECTED_EXAMPLES,
    OUTPUT_FORMAT_JSON,
    OUTPUT_FORMAT_TEXT,
    PROMPT_TEMPLATE_FILTERING_ANALYSIS,
    PROMPT_TEMPLATE_REVIEW,
    PROMPT_TEMPLATE_SUMMARIZATION,
    STATIC_COMMENT_EXAMPLES,
    TEMPLATE_COMMENT_EXAMPLE,
    TEMPLATE_PATCH_FROM_HUNK,
)
from bugbug.tools.code_review.utils import (
    format_patch_set,
    generate_processed_output,
    parse_model_output,
)
from bugbug.tools.core.data_types import InlineComment
from bugbug.tools.core.exceptions import LargeDiffError, ModelResultError
from bugbug.tools.core.llms import get_tokenizer
from bugbug.tools.core.platforms.base import Patch

logger = getLogger(__name__)

# Global variable for target software
TARGET_SOFTWARE: str | None = None


class CodeReviewTool(GenerativeModelTool):
    version = "0.0.1"

    def __init__(
        self,
        llm: BaseChatModel,
        function_search: Optional[FunctionSearch] = None,
        review_comments_db: Optional["ReviewCommentsDB"] = None,
        show_patch_example: bool = False,
        verbose: bool = True,
        suggestions_feedback_db: Optional["SuggestionsFeedbackDB"] = None,
        target_software: Optional[str] = None,
    ) -> None:
        super().__init__()

        self.target_software = target_software or TARGET_SOFTWARE

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

        experience_scope = (
            f"the {self.target_software} source code"
            if self.target_software
            else "a software project"
        )

        self.summarization_chain = LLMChain(
            prompt=PromptTemplate.from_template(
                PROMPT_TEMPLATE_SUMMARIZATION,
                partial_variables={"experience_scope": experience_scope},
            ),
            llm=llm,
            verbose=verbose,
        )
        self.filtering_chain = LLMChain(
            prompt=PromptTemplate.from_template(
                PROMPT_TEMPLATE_FILTERING_ANALYSIS,
                partial_variables={
                    "target_code_consistency": self.target_software or "rest of the"
                },
            ),
            llm=llm,
            verbose=verbose,
        )

        tools = [expand_context]
        if function_search:
            tools.append(create_find_function_definition_tool(function_search))

        self.agent = create_agent(
            llm,
            tools,
            system_prompt=f"You are an expert reviewer for {experience_scope}, with experience on source code reviews.",
        )

        self.review_comments_db = review_comments_db

        self.show_patch_example = show_patch_example

        self.verbose = verbose

        self.suggestions_feedback_db = suggestions_feedback_db

    def count_tokens(self, text):
        return len(self._tokenizer.encode(text))

    def generate_initial_prompt(
        self, patch: Patch, output_format: Literal["JSON", "TEXT"] = "JSON"
    ) -> str:
        formatted_patch = format_patch_set(patch.patch_set)

        output_summarization = self.summarization_chain.invoke(
            {
                "patch": formatted_patch,
                "bug_title": patch.bug_title,
                "patch_title": patch.patch_title,
            },
            return_only_outputs=True,
        )["text"]

        if self.verbose:
            GenerativeModelTool._print_answer(output_summarization)

        if output_format == "JSON":
            output_instructions = OUTPUT_FORMAT_JSON
        elif output_format == "TEXT":
            output_instructions = OUTPUT_FORMAT_TEXT
        else:
            raise ValueError(
                f"Unsupported output format: {output_format}, choose JSON or TEXT"
            )

        created_before = patch.date_created if self.is_experiment_env else None
        return PROMPT_TEMPLATE_REVIEW.format(
            patch=formatted_patch,
            patch_summarization=output_summarization,
            comment_examples=self._get_comment_examples(patch, created_before),
            approved_examples=self._get_generated_examples(patch, created_before),
            target_code_consistency=self.target_software or "rest of the",
            output_instructions=output_instructions,
            bug_title=patch.bug_title,
            patch_title=patch.patch_title,
            patch_url=patch.patch_url,
            target_software=self.target_software,
        )

    def _generate_suggestions(self, patch: Patch):
        try:
            for chunk in self.agent.stream(
                {
                    "messages": [
                        HumanMessage(self.generate_initial_prompt(patch)),
                    ]
                },
                context=CodeReviewContext(patch=patch),
                stream_mode="values",
                config={"recursion_limit": 50},
            ):
                result = chunk
        except GraphRecursionError as e:
            raise ModelResultError("The model could not complete the review") from e

        return result["messages"][-1].content

    def run(self, patch: Patch) -> list[InlineComment] | None:
        if self.count_tokens(patch.raw_diff) > 21000:
            raise LargeDiffError("The diff is too large")

        output = self._generate_suggestions(patch)

        unfiltered_suggestions = parse_model_output(output)
        if not unfiltered_suggestions:
            logger.info("No suggestions were generated")
            return []

        rejected_examples = (
            "\n    - ".join(self.get_similar_rejected_comments(unfiltered_suggestions))
            if self.suggestions_feedback_db
            else DEFAULT_REJECTED_EXAMPLES
        )

        raw_output = self.filtering_chain.invoke(
            {
                "comments": output,
                "rejected_examples": rejected_examples,
            },
            return_only_outputs=True,
        )["text"]

        if self.verbose:
            GenerativeModelTool._print_answer(raw_output)

        return list(generate_processed_output(raw_output, patch.patch_set))

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

    def get_similar_rejected_comments(self, suggestions) -> Iterable[str]:
        if not self.suggestions_feedback_db:
            raise Exception("Suggestions feedback database is not available")

        num_examples_per_suggestion = 10 // len(suggestions) or 1
        seen_ids: set[int] = set()

        for suggestion in suggestions:
            similar_rejected_suggestions = (
                self.suggestions_feedback_db.find_similar_rejected_suggestions(
                    suggestion["comment"],
                    limit=num_examples_per_suggestion,
                    excluded_ids=seen_ids,
                )
            )
            for rejected_suggestion in similar_rejected_suggestions:
                seen_ids.add(rejected_suggestion.id)
                yield rejected_suggestion.comment
