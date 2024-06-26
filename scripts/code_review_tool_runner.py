# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import sys

import scripts.review_comments_retriever as review_comments_retriever
from bugbug import generative_model_tool
from bugbug.embedding_model_tool import embedding_class
from bugbug.tools import code_review


def run(args) -> None:
    llm = generative_model_tool.create_llm(args.llm)

    code_review_tool = code_review.CodeReviewTool(llm)

    review_data = code_review.review_data_classes[args.review_platform]()

    revision = review_data.get_review_request_by_id(args.review_request_id)
    patch = review_data.get_patch_by_id(revision.patch_id)

    if args.prompt == "default":
        examples = None
    else:
        embedding = embedding_class[args.llm]()
        examples = review_comments_retriever.retrieve_examples(
            review_data, embedding, patch
        )

    print(patch)
    print(code_review_tool.run(patch, examples, args.prompt))
    input()


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--review_platform",
        help="Review platform",
        choices=list(code_review.review_data_classes.keys()),
    )
    parser.add_argument(
        "--review_request_id",
        help="Review request ID",
    )
    parser.add_argument(
        "--llm",
        help="LLM",
        choices=["human", "openai", "azureopenai", "llama2"],
    )
    parser.add_argument(
        "--prompt",
        help="Prompt approach to use",
        choices=["default", "rag_com", "rag_diff_com"],
    )
    return parser.parse_args(args)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    run(args)
