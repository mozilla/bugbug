# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import sys

from bugbug import generative_model_tool
from bugbug.tools import code_review


def run(args) -> None:
    llm = generative_model_tool.create_llm(args.llm)

    code_review_tool = code_review.CodeReviewTool(llm)

    review_data = code_review.review_data_classes[args.review_platform]()

    if args.review_platform == "swarm" and hasattr(
        review_data, "get_patch_by_version_fromto"
    ):
        patch = review_data.get_patch_by_version_fromto(
            args.review_request_id,
            version_before=args.version_before,
            version_num=args.version_num,
        )
    else:
        revision = review_data.get_review_request_by_id(args.review_request_id)
        patch = review_data.get_patch_by_id(revision.patch_id)

    print(patch)
    print(code_review_tool.run(patch))
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
        choices=["human", "openai", "llama2"],
    )
    parser.add_argument(
        "--version_before",
        help="Version of the review to compare to (swarm specific argument)",
        default=0,
        required=False,
    )
    parser.add_argument(
        "--version_num",
        help="Current version of the review (swarm specific argument)",
        default=1,
        required=False,
    )
    return parser.parse_args(args)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    run(args)
