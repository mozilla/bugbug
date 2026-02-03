from functools import cached_property
from logging import getLogger

import weave

from bugbug.tools.comment_matching.agent import CommentMatchingTool
from bugbug.tools.suggestion_filtering.agent import SuggestionFilteringTool

logger = getLogger(__name__)


class BasicMetricsScorer(weave.Scorer):
    """Score basic metrics: comment counts and error tracking."""

    @weave.op()
    def score(
        self,
        output: dict,
        ground_truth_comments: list[dict],
    ) -> dict:
        valid_comment_count = sum(
            comment["evaluation"] == "VALID" for comment in ground_truth_comments
        )
        invalid_comment_count = sum(
            comment["evaluation"] == "INVALID" for comment in ground_truth_comments
        )

        return {
            "generated_comment_count": len(output["comments"]),
            "ground_truth_valid_count": valid_comment_count,
            "ground_truth_invalid_count": invalid_comment_count,
            "ground_truth_total_count": len(ground_truth_comments),
        }

    def summarize(self, score_rows: list[dict]) -> dict:
        """Aggregate scores across all examples."""
        total_examples = len(score_rows)
        total_generated = sum(r.get("generated_comment_count", 0) for r in score_rows)
        total_gt_valid = sum(r.get("ground_truth_valid_count", 0) for r in score_rows)
        total_gt_invalid = sum(
            r.get("ground_truth_invalid_count", 0) for r in score_rows
        )
        total_gt = sum(r.get("ground_truth_total_count", 0) for r in score_rows)
        successful_runs = sum("generated_comment_count" in r for r in score_rows)
        error_count = total_examples - successful_runs

        return {
            "total_generated_comments": total_generated,
            "total_ground_truth_valid": total_gt_valid,
            "total_ground_truth_invalid": total_gt_invalid,
            "total_ground_truth": total_gt,
            "avg_generated_per_diff": (
                total_generated / successful_runs if successful_runs else 0
            ),
            "error_rate": error_count / total_examples if total_examples else 0,
            "num_examples": total_examples,
        }


class LLMCommentMatchingScorer(weave.Scorer):
    """Score comment matching using LLM-based semantic comparison.

    This scorer uses an LLM to match generated comments against ground truth
    comments, calculating recall and precision metrics.
    """

    @cached_property
    def matching_tool(self):
        return CommentMatchingTool.create()

    @cached_property
    def filtering_tool(self):
        return SuggestionFilteringTool.create()

    @weave.op()
    def score(
        self,
        output: dict,
        ground_truth_comments: list[dict],
        diff_id: int,
    ) -> dict:
        generated_comments = output["comments"]

        retained_indices = set(
            self.filtering_tool.get_indices_of_retained_comments(generated_comments)
        )
        retained_comments = [
            c for i, c in enumerate(generated_comments) if i in retained_indices
        ]
        excluded_comments = [
            c for i, c in enumerate(generated_comments) if i not in retained_indices
        ]

        old_comments = [
            {"id": i, "content": c["comment"], "file": c["file_path"]}
            for i, c in enumerate(ground_truth_comments)
        ]

        new_comments = [
            {"id": i, "content": c.comment, "file": c.file}
            for i, c in enumerate(generated_comments)
        ]

        matches = self.matching_tool.run(
            old_comments=old_comments, new_comments=new_comments
        )

        seen_old: set[int] = set()
        seen_new: set[int] = set()
        matched_valid_retained = []
        matched_valid_excluded = []
        matched_invalid_retained = []
        matched_invalid_excluded = []

        for match in matches:
            old_idx = match.old_comment_id
            new_idx = match.new_comment_id

            if old_idx >= len(ground_truth_comments) or new_idx >= len(
                generated_comments
            ):
                continue

            # Validate file match
            gt_comment = ground_truth_comments[old_idx]
            gen_comment = generated_comments[new_idx]

            if gt_comment["file_path"] != gen_comment.file:
                logger.debug(
                    f"File mismatch for diff {diff_id}: "
                    f"{gt_comment['file_path']} != {gen_comment.file}"
                )
                continue

            seen_old.add(old_idx)
            seen_new.add(new_idx)

            is_retained = new_idx in retained_indices
            match_comments = {
                "ground_truth_comment": gt_comment,
                "generated_comment": gen_comment,
            }

            if gt_comment["evaluation"] == "VALID":
                if is_retained:
                    matched_valid_retained.append(match_comments)
                else:
                    matched_valid_excluded.append(match_comments)
            else:
                if is_retained:
                    matched_invalid_retained.append(match_comments)
                else:
                    matched_invalid_excluded.append(match_comments)

        unmatched_gt_valid = []
        unmatched_gt_invalid = []

        for i in range(len(ground_truth_comments)):
            if i in seen_old:
                continue

            comment = ground_truth_comments[i]
            evaluation = ground_truth_comments[i]["evaluation"]
            if evaluation == "VALID":
                unmatched_gt_valid.append(comment)
            else:
                unmatched_gt_invalid.append(comment)

        unmatched_gen_retained = []
        unmatched_gen_excluded = []

        for i in range(len(generated_comments)):
            if i in seen_new:
                continue

            comment = new_comments[i]
            if i in retained_indices:
                unmatched_gen_retained.append(comment)
            else:
                unmatched_gen_excluded.append(comment)

        return {
            # Matched counts (derived from lists)
            "matched_valid_count": len(matched_valid_retained)
            + len(matched_valid_excluded),
            "matched_invalid_count": len(matched_invalid_retained)
            + len(matched_invalid_excluded),
            # Unmatched counts
            "unmatched_generated_count": len(unmatched_gen_retained)
            + len(unmatched_gen_excluded),
            "unmatched_ground_truth_valid_count": len(unmatched_gt_valid),
            "unmatched_ground_truth_invalid_count": len(unmatched_gt_invalid),
            # Unmatched details
            "unmatched_ground_truth_valid": unmatched_gt_valid,
            "unmatched_ground_truth_invalid": unmatched_gt_invalid,
            "unmatched_gen_retained": unmatched_gen_retained,
            "unmatched_gen_excluded": unmatched_gen_excluded,
            # Filtering metrics
            "filtering_retained_count": len(retained_comments),
            "filtering_excluded_count": len(excluded_comments),
            "filtering_retention_rate": (
                len(retained_comments) / len(generated_comments)
                if generated_comments
                else 0
            ),
            "filtering_retained_comments": retained_comments,
            "filtering_excluded_comments": excluded_comments,
            # Filtering x Matching breakdown (lists with details)
            "matched_valid_retained": matched_valid_retained,
            "matched_valid_excluded": matched_valid_excluded,
            "matched_invalid_retained": matched_invalid_retained,
            "matched_invalid_excluded": matched_invalid_excluded,
        }

    def summarize(self, score_rows: list[dict]) -> dict:
        total_matched_valid = sum(r.get("matched_valid_count", 0) for r in score_rows)
        total_matched_invalid = sum(
            r.get("matched_invalid_count", 0) for r in score_rows
        )
        total_unmatched_gen = sum(
            r.get("unmatched_generated_count", 0) for r in score_rows
        )
        total_unmatched_gt_valid = sum(
            r.get("unmatched_ground_truth_valid_count", 0) for r in score_rows
        )
        total_unmatched_gt_invalid = sum(
            r.get("unmatched_ground_truth_invalid_count", 0) for r in score_rows
        )

        total_gt_valid = total_matched_valid + total_unmatched_gt_valid
        total_gt_invalid = total_matched_invalid + total_unmatched_gt_invalid

        # Filtering aggregates
        total_retained = sum(r.get("filtering_retained_count", 0) for r in score_rows)
        total_excluded = sum(r.get("filtering_excluded_count", 0) for r in score_rows)
        total_generated = total_retained + total_excluded

        # Filtering x Matching aggregates (use len() since values are lists)
        total_matched_valid_retained = sum(
            len(r.get("matched_valid_retained", [])) for r in score_rows
        )
        total_matched_valid_excluded = sum(
            len(r.get("matched_valid_excluded", [])) for r in score_rows
        )
        total_matched_invalid_retained = sum(
            len(r.get("matched_invalid_retained", [])) for r in score_rows
        )
        total_matched_invalid_excluded = sum(
            len(r.get("matched_invalid_excluded", [])) for r in score_rows
        )

        return {
            "total_matched_valid": total_matched_valid,
            "total_matched_invalid": total_matched_invalid,
            "total_unmatched_generated": total_unmatched_gen,
            "recall_valid": (
                total_matched_valid / total_gt_valid if total_gt_valid > 0 else 0
            ),
            "recall_invalid": (
                total_matched_invalid / total_gt_invalid if total_gt_invalid > 0 else 0
            ),
            "missed_valid_rate": (
                total_unmatched_gt_valid / total_gt_valid if total_gt_valid > 0 else 0
            ),
            "missed_invalid_rate": (
                total_unmatched_gt_invalid / total_gt_invalid
                if total_gt_invalid > 0
                else 0
            ),
            # Filtering summary metrics
            "total_filtering_retained": total_retained,
            "total_filtering_excluded": total_excluded,
            "overall_retention_rate": (
                total_retained / total_generated if total_generated > 0 else 0
            ),
            # Filtering x Matching summary
            "total_matched_valid_retained": total_matched_valid_retained,
            "total_matched_valid_excluded": total_matched_valid_excluded,
            "total_matched_invalid_retained": total_matched_invalid_retained,
            "total_matched_invalid_excluded": total_matched_invalid_excluded,
            # Derived rates
            "false_exclusion_rate": (
                total_matched_valid_excluded / total_matched_valid
                if total_matched_valid > 0
                else 0
            ),
            "true_exclusion_rate": (
                total_matched_invalid_excluded / total_matched_invalid
                if total_matched_invalid > 0
                else 0
            ),
        }
