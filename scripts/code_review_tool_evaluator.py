# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""This script evaluates different variants of the code review tool.

Before running this script, you may need to set the following environment
variables:
    - BUGBUG_PHABRICATOR_URL
    - BUGBUG_PHABRICATOR_TOKEN
    - BUGBUG_*_API_KEY (replace * with your LLM provider)
    - BUGBUG_QDRANT_API_KEY
    - BUGBUG_QDRANT_LOCATION

To specify different variants to evaluate, please modify the get_tool_variants
function.
"""

from datetime import datetime, timedelta

import pandas as pd
from tabulate import tabulate

from bugbug import generative_model_tool, phabricator, utils
from bugbug.code_search.mozilla import FunctionSearchMozilla
from bugbug.tools import code_review
from bugbug.vectordb import QdrantVectorDB


def get_tool_variants(
    llm,
    variants: list[str] | None = None,
) -> list[tuple[str, code_review.CodeReviewTool]]:
    """Returns a list of tool variants to evaluate.

    Returns:
        List of tuples, where each tuple contains the name of the variant and
        and instance of the code review tool to evaluate.
    """

    def is_variant_selected(*target_variants):
        return variants is None or any(
            target_variant in variants for target_variant in target_variants
        )

    # Step 1: we start with instantiating the dependencies based on the selected
    # variants.

    if is_variant_selected("CONTEXT", "RAG and CONTEXT"):

        def get_file(commit_hash, path):
            r = utils.get_session("hgmo").get(
                f"https://hg.mozilla.org/mozilla-unified/raw-file/{commit_hash}/{path}",
                headers={
                    "User-Agent": utils.get_user_agent(),
                },
            )
            r.raise_for_status()
            return r.text

        repo_dir = "../mozilla-unified"
        function_search = FunctionSearchMozilla(repo_dir, get_file, True)

    if is_variant_selected("RAG", "RAG and CONTEXT"):
        vector_db = QdrantVectorDB("diff_comments")
        review_comments_db = code_review.ReviewCommentsDB(vector_db)

    # Step 2: we create the selected tool variants.

    tool_variants = []
    if is_variant_selected("RAG"):
        tool_variants.append(
            (
                "RAG",
                code_review.CodeReviewTool(
                    comment_gen_llms=[llm],
                    function_search=None,
                    review_comments_db=review_comments_db,
                ),
            )
        )

    if is_variant_selected("CONTEXT"):
        tool_variants.append(
            (
                "CONTEXT",
                code_review.CodeReviewTool(
                    comment_gen_llms=[llm],
                    function_search=function_search,
                    review_comments_db=None,
                ),
            )
        )

    if is_variant_selected("RAG and CONTEXT"):
        tool_variants.append(
            (
                "RAG and CONTEXT",
                code_review.CodeReviewTool(
                    comment_gen_llms=[llm],
                    function_search=function_search,
                    review_comments_db=review_comments_db,
                ),
            )
        )

    return tool_variants


def get_review_requests_sample(since: timedelta, limit: int):
    start_date = (datetime.now() - since).timestamp()
    MOZILLA_CENTRAL_PHID = "PHID-REPO-saax4qdxlbbhahhp2kg5"

    n = 0
    for review_request in phabricator.get_revisions():
        if (
            review_request["fields"]["repositoryPHID"] != MOZILLA_CENTRAL_PHID
            or review_request["fields"]["dateCreated"] <= start_date
        ):
            continue

        if n >= limit >= 0:
            break

        yield review_request["id"]
        n += 1


def print_prettified_comments(comments: list[code_review.InlineComment]):
    if not comments:
        print("No comments to show.")
        return

    print(
        tabulate(
            [
                (
                    comment.filename,
                    comment.end_line,
                    comment.content,
                )
                for comment in comments
            ],
            headers=[
                "File",
                "Line",
                "Comment",
            ],
            maxcolwidths=[
                30,
                10,
                100,
            ],
        ),
    )


def main(args):
    review_platform = "phabricator"
    review_data: code_review.ReviewData = code_review.review_data_classes[
        review_platform
    ]()

    tool_variants = get_tool_variants(
        generative_model_tool.create_llm_from_args(args), args.variants
    )

    is_first_result = True
    result_file = "code_review_tool_evaluator.csv"
    result_unique_columns = ["Review Request ID", "File", "Line", "Comment Number"]
    result_all_columns = result_unique_columns + [
        f"Comment ({variant_name})" for variant_name, _ in tool_variants
    ]

    sample_ids = (
        args.review_request_ids
        if args.review_request_ids
        else get_review_requests_sample(timedelta(days=60), 3)
    )

    for review_request_id in sample_ids:
        print("---------------------------------------------------------")
        print(f"Review Request ID: {review_request_id}")
        review_request = review_data.get_review_request_by_id(review_request_id)
        print(f"Patch ID: {review_request.patch_id}")
        patch = review_data.get_patch_by_id(review_request.patch_id)
        print("---------------------------------------------------------")

        if len(patch.raw_diff) > 20_000:
            print("Skipping the patch because it is too large.")
            continue

        all_variants_results = []
        for variant_name, tool in tool_variants:
            print(f"\n\nVariant: {variant_name}\n")
            try:
                comments = tool.run(patch)
            except code_review.FileNotInPatchError as e:
                print("Error while running the tool:", e)
                continue

            print_prettified_comments(comments)

            all_variants_results.extend(
                {
                    "Review Request ID": review_request_id,
                    "File": comment.filename,
                    "Line": comment.end_line,
                    "Comment Number": i + 1,
                    f"Comment ({variant_name})": comment.content,
                }
                for i, comment in enumerate(comments)
            )

        df = (
            pd.DataFrame(all_variants_results, columns=result_all_columns)
            .groupby(result_unique_columns)
            .first()
        )
        df.to_csv(
            result_file,
            header=is_first_result,
            mode="w" if is_first_result else "a",
        )
        if is_first_result:
            is_first_result = False
            print("You can find the results in the file:", result_file)

        print("\n\n\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    generative_model_tool.create_llm_to_args(parser)
    parser.add_argument(
        "-v",
        "--variant",
        dest="variants",
        action="append",
        help="if specified, run only the selected variant(s)",
        metavar="VARIANT",
    )
    parser.add_argument(
        "-r",
        "--revision-id",
        dest="review_request_ids",
        action="append",
        help="if specified, run only the selected Revision ID(s)",
        metavar="REVISION_ID",
        type=int,
    )

    args = parser.parse_args()

    main(args)
