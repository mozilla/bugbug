# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""This script evaluates different variants of the code review tool.

Before running this script, you may need to set the following environment
variables:
    - BUGBUG_PHABRICATOR_URL
    - BUGBUG_PHABRICATOR_TOKEN
    - BUGBUG_OPENAI_API_KEY
    - BUGBUG_QDRANT_API_KEY
    - BUGBUG_QDRANT_LOCATION

To specify different variants to evaluate, please modify the get_tool_variants
function.
"""

from datetime import datetime, timedelta

import pandas as pd
import requests
from tabulate import tabulate

from bugbug import generative_model_tool, phabricator
from bugbug.code_search.mozilla import FunctionSearchMozilla
from bugbug.tools import code_review
from bugbug.vectordb import QdrantVectorDB


def get_tool_variants() -> list[tuple[str, code_review.CodeReviewTool]]:
    """Returns a list of tool variants to evaluate.

    Returns:
        List of tuples, where each tuple contains the name of the variant and
        and instance of the code review tool to evaluate.
    """
    llm = generative_model_tool.create_llm("openai")

    def get_file(commit_hash, path):
        r = requests.get(
            f"https://hg.mozilla.org/mozilla-unified/raw-file/{commit_hash}/{path}"
        )
        r.raise_for_status()
        return r.text

    repo_dir = "../mozilla-unified"

    function_search = FunctionSearchMozilla(repo_dir, get_file, True)

    vector_db = QdrantVectorDB("diff_comments")
    review_comments_db = code_review.ReviewCommentsDB(vector_db)

    return [
        (
            "With related comments",
            code_review.CodeReviewTool(
                llm=llm,
                function_search=function_search,
                review_comments_db=review_comments_db,
            ),
        ),
        (
            "With static list of comments",
            code_review.CodeReviewTool(
                llm=llm,
                function_search=function_search,
                review_comments_db=None,
            ),
        ),
    ]


def get_review_requests_sample(since: timedelta, limit: int):
    start_date = (datetime.now() - since).timestamp()

    n = 0
    for review_request in phabricator.get_revisions():
        if review_request["fields"]["dateCreated"] <= start_date:
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


def main():
    review_platform = "phabricator"
    review_data: code_review.ReviewData = code_review.review_data_classes[
        review_platform
    ]()

    tool_variants = get_tool_variants()

    is_first_result = True
    result_file = "code_review_tool_evaluator.csv"
    result_columns = ["Review Request ID", "File", "Line"] + [
        f"Comment ({variant_name})" for variant_name, _ in tool_variants
    ]

    for review_request_id in get_review_requests_sample(timedelta(days=60), 3):
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
            comments = tool.run(patch)
            print_prettified_comments(comments)

            all_variants_results.extend(
                {
                    "Review Request ID": review_request_id,
                    "File": comment.filename,
                    "Line": comment.end_line,
                    f"Comment ({variant_name})": comment.content,
                }
                for comment in comments
            )

        df = pd.DataFrame(all_variants_results, columns=result_columns).groupby(
            ["Review Request ID", "File", "Line"]
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
    main()
