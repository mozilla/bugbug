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

import os
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableSequence
from tabulate import tabulate

from bugbug import db, generative_model_tool, phabricator, utils
from bugbug.code_search.mozilla import FunctionSearchMozilla
from bugbug.tools import code_review
from bugbug.vectordb import QdrantVectorDB

code_review.TARGET_SOFTWARE = "Mozilla Firefox"
VERBOSE_CODE_REVIEW = False


EVALUATION_TEMPLATE = """Your are an expert in code review at Mozilla Firefox.

**Task**:

Match two sets of code review comments to identify redundant comments.

**Instructions**:

    1. **Consider the following about all comments**:
        - The comments are related to the same code patch.
        - The comments may be written in different styles.

    2. **Understand what each comment is addressing**:
        - Read the comments in both sets.
        - Understand the issue that each comment is addressing.

    3. **Check for matches**:
        - If you find a comment in the old set that is addressing the same issue as a comment in the new set, link them as redundant.
        - The comments may not be identical, but they should be addressing the same issue.
        - The level of detail in the comments may vary.

    4. **Output format**:
        - Output a list of matched comments.
        - Include the comment IDs only in the output.
        - Each element in the list should be an object with two keys: `old_comment_id` and `new_comment_id`.
        - No explanation is needed in the output, only the IDs of the matched comments.
        - The output should be a valid json only.


**Output example**:

    [
        {{"old_comment_id": 1, "new_comment_id": 3}},
        {{"old_comment_id": 4, "new_comment_id": 2}},
    ]

**First set of comments (old comments)**:

{old_comments}

**Second set of comments (new comments)**:

{new_comments}
"""


class FeedbackEvaluator:
    def __init__(self, evaluation_dataset: str):
        self.evaluated_comments = pd.read_csv(evaluation_dataset)

        llm = generative_model_tool.create_openai_llm()
        evaluate_comments_prompt = PromptTemplate.from_template(EVALUATION_TEMPLATE)
        self.evaluation_chain = RunnableSequence(evaluate_comments_prompt, llm)

    def evaluate_diff_comments(
        self,
        diff_id: int,
        new_comments: list[code_review.InlineComment],
    ) -> list[dict]:
        diff_evaluated_comments = self.evaluated_comments[
            self.evaluated_comments["diff_id"] == diff_id
        ].reset_index()
        diff_evaluated_comments["evaluation"] = np.where(
            diff_evaluated_comments["evaluation"].isin(["CORRECT", "VALID_REDUNDANT"]),
            "VALID",
            "INVALID",
        )

        output = self.evaluation_chain.invoke(
            {
                "old_comments": [
                    {
                        "id": i,
                        "content": raw["comment"],
                        "file": raw["file_path"],
                    }
                    for i, raw in diff_evaluated_comments.iterrows()
                ],
                "new_comments": [
                    {
                        "id": i,
                        "content": comment.content,
                        "file": comment.filename,
                    }
                    for i, comment in enumerate(new_comments)
                ],
            }
        )

        matches = code_review.parse_model_output(output.content)

        results = [
            {
                "new_comment": comment.content,
                "old_comments_count": 0,
                "matched": False,
            }
            for comment in new_comments
        ]
        seen_old_comments = set()

        for match in matches:
            old_index = match["old_comment_id"]
            new_index = match["new_comment_id"]

            evaluated_comment = diff_evaluated_comments.iloc[old_index]
            new_comment = new_comments[new_index]

            if evaluated_comment["file_path"] != new_comment.filename:
                print(
                    "File mismatch:",
                    evaluated_comment["file_path"],
                    new_comment.filename,
                )
                continue

            current_result = results[new_index]

            current_result["evaluation"] = (
                "MIXED"
                if (
                    "evaluation" in current_result
                    and current_result["evaluation"] != evaluated_comment["evaluation"]
                )
                else evaluated_comment["evaluation"]
            )

            if "old_comment" in current_result:
                current_result["old_comment"] += (
                    f"\n\n-------------\n\n{evaluated_comment['comment']}"
                )
            else:
                current_result["old_comment"] = evaluated_comment["comment"]

            current_result["old_comments_count"] += 1
            current_result["matched"] = True

            seen_old_comments.add(old_index)

        for i, raw in diff_evaluated_comments.iterrows():
            if i in seen_old_comments:
                continue

            results.append(
                {
                    "old_comment": raw["comment"],
                    "evaluation": raw["evaluation"],
                    "old_comments_count": 1,
                }
            )

        self.print_evaluation_matches(results)

        return results

    @staticmethod
    def print_evaluation_matches(matching_results: list[dict]):
        print(
            tabulate(
                [
                    (
                        result.get("new_comment", ""),
                        result.get("old_comment", ""),
                        result.get("evaluation", ""),
                    )
                    for result in matching_results
                ],
                tablefmt="mixed_grid",
                headers=[
                    "New Comment",
                    "Old Comment",
                    "Evaluation",
                ],
                maxcolwidths=[
                    60,
                    60,
                    20,
                ],
            )
        )


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

    if is_variant_selected(
        "CONTEXT", "RAG and CONTEXT", "RAG and CONTEXT and REJECTED_COMMENTS"
    ):

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

    if is_variant_selected(
        "RAG", "RAG and CONTEXT", "RAG and CONTEXT and REJECTED_COMMENTS", "llm-gpt-4.1"
    ):
        review_comments_db = code_review.ReviewCommentsDB(
            QdrantVectorDB("diff_comments")
        )

    if is_variant_selected("RAG and CONTEXT and REJECTED_COMMENTS", "llm-gpt-4.1"):
        suggestions_feedback_db = code_review.SuggestionsFeedbackDB(
            QdrantVectorDB("suggestions_feedback")
        )

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
                    verbose=VERBOSE_CODE_REVIEW,
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
                    verbose=VERBOSE_CODE_REVIEW,
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
                    verbose=VERBOSE_CODE_REVIEW,
                ),
            )
        )

    if is_variant_selected("RAG and CONTEXT and REJECTED_COMMENTS"):
        tool_variants.append(
            (
                "RAG and CONTEXT and REJECTED_COMMENTS",
                code_review.CodeReviewTool(
                    comment_gen_llms=[llm],
                    function_search=function_search,
                    review_comments_db=review_comments_db,
                    suggestions_feedback_db=suggestions_feedback_db,
                    verbose=VERBOSE_CODE_REVIEW,
                ),
            ),
        )

    if is_variant_selected("llm-gpt-4.1"):
        tool_variants.append(
            (
                "llm-gpt-4.1",
                code_review.CodeReviewTool(
                    comment_gen_llms=[
                        generative_model_tool.create_openai_llm(
                            model_name="gpt-4.1-2025-04-14"
                        )
                    ],
                    # function_search=function_search,
                    review_comments_db=review_comments_db,
                    suggestions_feedback_db=suggestions_feedback_db,
                    verbose=VERBOSE_CODE_REVIEW,
                ),
            )
        )

    return tool_variants


def get_review_requests_sample(since: timedelta, limit: int):
    assert db.download(phabricator.REVISIONS_DB)

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


def get_latest_evaluation_results_file(results_dir: str | None):
    import glob
    import os

    files = glob.glob("evaluation_results_*#*.csv", root_dir=results_dir)
    if not files:
        raise FileNotFoundError("No evaluation results file found.")

    latests_files = max(files)
    if results_dir:
        return os.path.join(results_dir, latests_files)

    return latests_files


def main(args):
    review_platform = "phabricator"
    review_data: code_review.ReviewData = code_review.review_data_classes[
        review_platform
    ]()

    tool_variants = get_tool_variants(
        generative_model_tool.create_llm_from_args(args), args.variants
    )

    evaluator = FeedbackEvaluator(args.evaluation_dataset)

    is_first_result = True
    result_file = os.path.join(
        args.results_dir,
        "code_review_tool_evaluator.csv",
    )
    evaluation_results_file = os.path.join(
        args.results_dir,
        f"evaluation_results_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.csv",
    )
    result_unique_columns = ["Review Request ID", "File", "Line", "Comment Number"]
    result_all_columns = result_unique_columns + [
        f"{title} ({variant_name})"
        for variant_name, _ in tool_variants
        for title in ("Comment", "Evaluation")
    ]
    evaluation_result_all_columns = [
        "variant_name",
        "revision_id",
        "diff_id",
        "new_comment",
        "old_comments_count",
        "matched",
        "old_comment",
        "evaluation",
    ]

    selected_review_requests = []
    if args.diff_ids:
        selected_review_requests = (
            ("n/a", code_review.ReviewRequest(diff_id)) for diff_id in args.diff_ids
        )
    elif args.review_request_ids:
        selected_review_requests = (
            (review_request_id, review_data.get_review_request_by_id(review_request_id))
            for review_request_id in args.review_request_ids
        )
    elif args.evaluation_strategy == "random":
        print("No review request IDs specified. Selecting a random sample.")
        selected_review_requests = (
            (revision_id, code_review.ReviewRequest(diff_id))
            for revision_id, diff_id in evaluator.evaluated_comments.query(
                "evaluation == 'CORRECT'"
            )[["revision_id", "diff_id"]]
            .drop_duplicates()
            .sample(20)
            .itertuples(index=False)
        )
    elif args.evaluation_strategy == "same":
        selected_review_requests = (
            (revision_id, code_review.ReviewRequest(diff_id))
            for revision_id, diff_id in pd.read_csv(
                get_latest_evaluation_results_file(args.results_dir),
            )[["revision_id", "diff_id"]]
            .drop_duplicates()
            .itertuples(name=None, index=False)
        )
    else:
        raise ValueError(
            "Please specify either --diff-id or --revision-id. Alternatively, use --evaluation-strategy."
        )

    for review_request_id, review_request in selected_review_requests:
        print("---------------------------------------------------------")
        print(f"Review Request ID: {review_request_id}")
        print(f"Patch ID: {review_request.patch_id}")
        patch = review_data.get_patch_by_id(review_request.patch_id)
        print("---------------------------------------------------------")

        if len(patch.raw_diff) > 20_000:
            print("Skipping the patch because it is too large.")
            continue

        all_variants_results = []
        all_variants_evaluation_results = []
        for variant_name, tool in tool_variants:
            print(f"\n\nVariant: {variant_name}\n")
            try:
                comments = tool.run(patch)
            except code_review.FileNotInPatchError as e:
                print("Error while running the tool:", e)
                continue
            except code_review.LargeDiffError:
                print("Skipping the patch because it is too large.")
                continue

            print_prettified_comments(comments)
            comment_per_line_counter = defaultdict(int)

            evaluation = evaluator.evaluate_diff_comments(
                review_request.patch_id, comments
            )

            all_variants_evaluation_results.extend(
                {
                    "variant_name": variant_name,
                    "revision_id": review_request_id,
                    "diff_id": review_request.patch_id,
                    **row,
                }
                for row in evaluation
            )

            for i, comment in enumerate(comments):
                key = (review_request_id, comment.filename, comment.end_line)
                comment_per_line_counter[key] += 1

                all_variants_results.append(
                    {
                        "Review Request ID": review_request_id,
                        "File": comment.filename,
                        "Line": comment.end_line,
                        "Comment Number": comment_per_line_counter[key],
                        f"Comment ({variant_name})": comment.content,
                        f"Evaluation ({variant_name})": evaluation[i].get("evaluation"),
                    }
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

        df = pd.DataFrame(
            all_variants_evaluation_results, columns=evaluation_result_all_columns
        )
        df.to_csv(
            evaluation_results_file,
            index=False,
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
    parser.add_argument(
        "-d",
        "--diff-id",
        dest="diff_ids",
        action="append",
        help="if specified, run only the selected Diff ID(s)",
        metavar="DIFF_ID",
        type=int,
    )
    parser.add_argument(
        "--evaluation-data",
        dest="evaluation_dataset",
        action="store",
        help="the path or the URL to a evaluation dataset in CSV format",
    )

    parser.add_argument(
        "--results-dir",
        dest="results_dir",
        action="store",
        help="the directory to store the results and read previous results",
    )

    parser.add_argument(
        "--evaluation-strategy",
        dest="evaluation_strategy",
        action="store",
        help="the evaluation strategy to use",
    )

    args = parser.parse_args()

    if args.diff_ids and args.review_request_ids:
        parser.error("Please specify either --diff-id or --revision-id, not both.")

    main(args)
