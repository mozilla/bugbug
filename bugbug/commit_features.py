# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
import sys
from collections import defaultdict
from typing import Sequence

import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

from bugbug import repository

EXPERIENCE_TIMESPAN = 90
EXPERIENCE_TIMESPAN_TEXT = f"{EXPERIENCE_TIMESPAN}_days"


class source_code_files_modified_num(object):
    name = "# of modified code files"

    def __call__(self, commit, **kwargs):
        return commit["source_code_files_modified_num"]


class other_files_modified_num(object):
    name = "# of modified non-code files"

    def __call__(self, commit, **kwargs):
        return commit["other_files_modified_num"]


class test_files_modified_num(object):
    name = "# of modified test files"

    def __call__(self, commit, **kwargs):
        return commit["test_files_modified_num"]


class source_code_file_size(object):
    def __call__(self, commit, **kwargs):
        return {
            "Total code files size": commit["total_source_code_file_size"],
            "Average code files size": commit["average_source_code_file_size"],
            "Maximum code files size": commit["maximum_source_code_file_size"],
            "Minimum code files size": commit["minimum_source_code_file_size"],
        }


class other_file_size(object):
    def __call__(self, commit, **kwargs):
        return {
            "Total non-code files size": commit["total_other_file_size"],
            "Average non-code files size": commit["average_other_file_size"],
            "Maximum non-code files size": commit["maximum_other_file_size"],
            "Minimum non-code files size": commit["minimum_other_file_size"],
        }


class test_file_size(object):
    def __call__(self, commit, **kwargs):
        return {
            "Total test files size": commit["total_test_file_size"],
            "Average test files size": commit["average_test_file_size"],
            "Maximum test files size": commit["maximum_test_file_size"],
            "Minimum test files size": commit["minimum_test_file_size"],
        }


class source_code_added(object):
    name = "# of code lines added"

    def __call__(self, commit, **kwargs):
        return commit["source_code_added"]


class other_added(object):
    name = "# of non-code lines added"

    def __call__(self, commit, **kwargs):
        return commit["other_added"]


class test_added(object):
    name = "# of lines added in tests"

    def __call__(self, commit, **kwargs):
        return commit["test_added"]


class source_code_deleted(object):
    name = "# of code lines deleted"

    def __call__(self, commit, **kwargs):
        return commit["source_code_deleted"]


class other_deleted(object):
    name = "# of non-code lines deleted"

    def __call__(self, commit, **kwargs):
        return commit["other_deleted"]


class test_deleted(object):
    name = "# of lines deleted in tests"

    def __call__(self, commit, **kwargs):
        return commit["test_deleted"]


class functions_touched_num(object):
    name = "# of functions touched"

    def __call__(self, commit, **kwargs):
        return sum(1 for f_group in commit["functions"].values() for f in f_group)


class functions_touched_size(object):
    def __call__(self, commit, **kwargs):
        function_sizes = [
            f["end"] - f["start"] + 1
            for f_group in commit["functions"].values()
            for f in f_group
        ]

        return {
            "Total functions size": sum(function_sizes),
            "Average functions size": sum(function_sizes) / len(function_sizes)
            if len(function_sizes) > 0
            else 0,
            "Maximum functions size": max(function_sizes, default=0),
            "Minimum functions size": min(function_sizes, default=0),
        }


class source_code_file_metrics(object):
    name = "metrics on source code file"

    def __call__(self, commit, **kwargs):
        return {
            "Average file cyclomatic": commit["metrics"]["cyclomatic_avg"],
            "Average file number of unique operands": commit["metrics"][
                "halstead_n2_avg"
            ],
            "Average file number of operands": commit["metrics"]["halstead_N2_avg"],
            "Average file number of unique operators": commit["metrics"][
                "halstead_n1_avg"
            ],
            "Average file number of operators": commit["metrics"]["halstead_N1_avg"],
            "Average file number of source loc": commit["metrics"]["sloc_avg"],
            "Average file number of instruction loc": commit["metrics"]["ploc_avg"],
            "Average file number of logical loc": commit["metrics"]["lloc_avg"],
            "Average file number of comment loc": commit["metrics"]["cloc_avg"],
            "Average file number of function arguments": commit["metrics"]["nargs_avg"],
            "Average file number of function exit points": commit["metrics"][
                "nexits_avg"
            ],
            "Average file cognitive": commit["metrics"]["cognitive_avg"],
            "Average file mi_original": commit["metrics"]["mi_original_avg"],
            "Average file mi_sei": commit["metrics"]["mi_sei_avg"],
            "Average file mi_visual_studio": commit["metrics"]["mi_visual_studio_avg"],
            "Maximum file cyclomatic": commit["metrics"]["cyclomatic_max"],
            "Maximum file number of unique operands": commit["metrics"][
                "halstead_n2_max"
            ],
            "Maximum file number of operands": commit["metrics"]["halstead_N2_max"],
            "Maximum file number of unique operators": commit["metrics"][
                "halstead_n1_max"
            ],
            "Maximum file number of operators": commit["metrics"]["halstead_N1_max"],
            "Maximum file number of source loc": commit["metrics"]["sloc_max"],
            "Maximum file number of instruction loc": commit["metrics"]["ploc_max"],
            "Maximum file number of logical loc": commit["metrics"]["lloc_max"],
            "Maximum file number of comment loc": commit["metrics"]["cloc_max"],
            "Maximum file number of function arguments": commit["metrics"]["nargs_max"],
            "Maximum file number of function exit points": commit["metrics"][
                "nexits_max"
            ],
            "Maximum file cognitive": commit["metrics"]["cognitive_max"],
            "Maximum file mi_original": commit["metrics"]["mi_original_max"],
            "Maximum file mi_sei": commit["metrics"]["mi_sei_max"],
            "Maximum file mi_visual_studio": commit["metrics"]["mi_visual_studio_max"],
            "Minimum file cyclomatic": commit["metrics"]["cyclomatic_min"],
            "Minimum file number of unique operands": commit["metrics"][
                "halstead_n2_min"
            ],
            "Minimum file number of operands": commit["metrics"]["halstead_N2_min"],
            "Minimum file number of unique operators": commit["metrics"][
                "halstead_n1_min"
            ],
            "Minimum file number of operators": commit["metrics"]["halstead_N1_min"],
            "Minimum file number of source loc": commit["metrics"]["sloc_min"],
            "Minimum file number of instruction loc": commit["metrics"]["ploc_min"],
            "Minimum file number of logical loc": commit["metrics"]["lloc_min"],
            "Minimum file number of comment loc": commit["metrics"]["cloc_min"],
            "Minimum file number of function arguments": commit["metrics"]["nargs_min"],
            "Minimum file number of function exit points": commit["metrics"][
                "nexits_min"
            ],
            "Minimum file cognitive": commit["metrics"]["cognitive_min"],
            "Minimum file mi_original": commit["metrics"]["mi_original_min"],
            "Minimum file mi_sei": commit["metrics"]["mi_sei_min"],
            "Minimum file mi_visual_studio": commit["metrics"]["mi_visual_studio_min"],
            "Total file cyclomatic": commit["metrics"]["cyclomatic_total"],
            "Total file number of unique operands": commit["metrics"][
                "halstead_n2_total"
            ],
            "Total file number of operands": commit["metrics"]["halstead_N2_total"],
            "Total file number of unique operators": commit["metrics"][
                "halstead_n1_total"
            ],
            "Total file number of operators": commit["metrics"]["halstead_N1_total"],
            "Total file number of source loc": commit["metrics"]["sloc_total"],
            "Total file number of instruction loc": commit["metrics"]["ploc_total"],
            "Total file number of logical loc": commit["metrics"]["lloc_total"],
            "Total file number of comment loc": commit["metrics"]["cloc_total"],
            "Total file number of function arguments": commit["metrics"]["nargs_total"],
            "Total file number of function exit points": commit["metrics"][
                "nexits_total"
            ],
            "Total file cognitive": commit["metrics"]["cognitive_total"],
            "Total file mi_original": commit["metrics"]["mi_original_total"],
            "Total file mi_sei": commit["metrics"]["mi_sei_total"],
            "Total file mi_visual_studio": commit["metrics"]["mi_visual_studio_total"],
        }


def merge_function_metrics(objects):
    metrics = {}

    for metric in repository.METRIC_NAMES:
        metrics.update(
            {
                f"{metric}_avg": sum(
                    obj["metrics"][f"{metric}_total"] for obj in objects
                )
                / len(objects)
                if len(objects) > 0
                else 0.0,
                f"{metric}_max": max(
                    (obj["metrics"][f"{metric}_total"] for obj in objects), default=0
                ),
                f"{metric}_min": min(
                    (obj["metrics"][f"{metric}_total"] for obj in objects), default=0
                ),
                f"{metric}_total": sum(
                    obj["metrics"][f"{metric}_total"] for obj in objects
                ),
            }
        )

    return metrics


class source_code_function_metrics(object):
    name = "metrics on source code functions"

    def __call__(self, commit, **kwargs):
        merged_metrics = merge_function_metrics(
            [func for funcs in commit["functions"].values() for func in funcs]
        )

        return {
            "Average function cyclomatic": merged_metrics["cyclomatic_avg"],
            "Average function number of unique operands": merged_metrics[
                "halstead_n2_avg"
            ],
            "Average function number of operands": merged_metrics["halstead_N2_avg"],
            "Average function number of unique operators": merged_metrics[
                "halstead_n1_avg"
            ],
            "Average function number of operators": merged_metrics["halstead_N1_avg"],
            "Average function number of source loc": merged_metrics["sloc_avg"],
            "Average function number of instruction loc": merged_metrics["ploc_avg"],
            "Average function number of logical loc": merged_metrics["lloc_avg"],
            "Average function number of comment loc": merged_metrics["cloc_avg"],
            "Average function number of function arguments": merged_metrics[
                "nargs_avg"
            ],
            "Average function number of function exit points": merged_metrics[
                "nexits_avg"
            ],
            "Average function cognitive": merged_metrics["cognitive_avg"],
            "Average function mi_original": merged_metrics["mi_original_avg"],
            "Average function mi_sei": merged_metrics["mi_sei_avg"],
            "Average function mi_visual_studio": merged_metrics["mi_visual_studio_avg"],
            "Maximum function cyclomatic": merged_metrics["cyclomatic_max"],
            "Maximum function number of unique operands": merged_metrics[
                "halstead_n2_max"
            ],
            "Maximum function number of operands": merged_metrics["halstead_N2_max"],
            "Maximum function number of unique operators": merged_metrics[
                "halstead_n1_max"
            ],
            "Maximum function number of operators": merged_metrics["halstead_N1_max"],
            "Maximum function number of source loc": merged_metrics["sloc_max"],
            "Maximum function number of instruction loc": merged_metrics["ploc_max"],
            "Maximum function number of logical loc": merged_metrics["lloc_max"],
            "Maximum function number of comment loc": merged_metrics["cloc_max"],
            "Maximum function number of function arguments": merged_metrics[
                "nargs_max"
            ],
            "Maximum function number of function exit points": merged_metrics[
                "nexits_max"
            ],
            "Maximum function cognitive": merged_metrics["cognitive_max"],
            "Maximum function mi_original": merged_metrics["mi_original_max"],
            "Maximum function mi_sei": merged_metrics["mi_sei_max"],
            "Maximum function mi_visual_studio": merged_metrics["mi_visual_studio_max"],
            "Minimum function cyclomatic": merged_metrics["cyclomatic_min"],
            "Minimum function number of unique operands": merged_metrics[
                "halstead_n2_min"
            ],
            "Minimum function number of operands": merged_metrics["halstead_N2_min"],
            "Minimum function number of unique operators": merged_metrics[
                "halstead_n1_min"
            ],
            "Minimum function number of operators": merged_metrics["halstead_N1_min"],
            "Minimum function number of source loc": merged_metrics["sloc_min"],
            "Minimum function number of instruction loc": merged_metrics["ploc_min"],
            "Minimum function number of logical loc": merged_metrics["lloc_min"],
            "Minimum function number of comment loc": merged_metrics["cloc_min"],
            "Minimum function number of function arguments": merged_metrics[
                "nargs_min"
            ],
            "Minimum function number of function exit points": merged_metrics[
                "nexits_min"
            ],
            "Minimum function cognitive": merged_metrics["cognitive_min"],
            "Minimum function mi_original": merged_metrics["mi_original_min"],
            "Minimum function mi_sei": merged_metrics["mi_sei_min"],
            "Minimum function mi_visual_studio": merged_metrics["mi_visual_studio_min"],
            "Total function cyclomatic": merged_metrics["cyclomatic_total"],
            "Total function number of unique operands": merged_metrics[
                "halstead_n2_total"
            ],
            "Total function number of operands": merged_metrics["halstead_N2_total"],
            "Total function number of unique operators": merged_metrics[
                "halstead_n1_total"
            ],
            "Total function number of operators": merged_metrics["halstead_N1_total"],
            "Total function number of source loc": merged_metrics["sloc_total"],
            "Total function number of instruction loc": merged_metrics["ploc_total"],
            "Total function number of logical loc": merged_metrics["lloc_total"],
            "Total function number of comment loc": merged_metrics["cloc_total"],
            "Total function number of function arguments": merged_metrics[
                "nargs_total"
            ],
            "Total function number of function exit points": merged_metrics[
                "nexits_total"
            ],
            "Total function cognitive": merged_metrics["cognitive_total"],
            "Total function mi_original": merged_metrics["mi_original_total"],
            "Total function mi_sei": merged_metrics["mi_sei_total"],
            "Total function mi_visual_studio": merged_metrics["mi_visual_studio_total"],
        }


class source_code_metrics_diff(object):
    name = "diff in metrics on source code"

    def __call__(self, commit, **kwargs):
        return {
            "Diff in cyclomatic": commit["metrics_diff"]["cyclomatic_total"],
            "Diff in number of unique operands": commit["metrics_diff"][
                "halstead_n2_total"
            ],
            "Diff in number of operands": commit["metrics_diff"]["halstead_N2_total"],
            "Diff in number of unique operators": commit["metrics_diff"][
                "halstead_n1_total"
            ],
            "Diff in number of operators": commit["metrics_diff"]["halstead_N1_total"],
            "Diff in number of source loc": commit["metrics_diff"]["sloc_total"],
            "Diff in number of instruction loc": commit["metrics_diff"]["ploc_total"],
            "Diff in number of logical loc": commit["metrics_diff"]["lloc_total"],
            "Diff in number of comment loc": commit["metrics_diff"]["cloc_total"],
            "Diff in number of function arguments": commit["metrics_diff"][
                "nargs_total"
            ],
            "Diff in number of function exit points": commit["metrics_diff"][
                "nexits_total"
            ],
            "Diff in cognitive": commit["metrics_diff"]["cognitive_total"],
            "Diff in mi_original": commit["metrics_diff"]["mi_original_total"],
            "Diff in mi_sei": commit["metrics_diff"]["mi_sei_total"],
            "Diff in mi_visual_studio": commit["metrics_diff"][
                "mi_visual_studio_total"
            ],
        }


def get_exps(exp_type, commit):
    items_key = f"{exp_type}s" if exp_type != "directory" else "directories"
    items_num = len(commit[items_key])

    return {
        "sum": commit[f"touched_prev_total_{exp_type}_sum"],
        "max": commit[f"touched_prev_total_{exp_type}_max"],
        "min": commit[f"touched_prev_total_{exp_type}_min"],
        "avg": commit[f"touched_prev_total_{exp_type}_sum"] / items_num
        if items_num > 0
        else 0,
        "sum backout": commit[f"touched_prev_total_{exp_type}_backout_sum"],
        "max backout": commit[f"touched_prev_total_{exp_type}_backout_max"],
        "min backout": commit[f"touched_prev_total_{exp_type}_backout_min"],
        "avg backout": commit[f"touched_prev_total_{exp_type}_backout_sum"] / items_num
        if items_num > 0
        else 0,
        f"sum {EXPERIENCE_TIMESPAN_TEXT}": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_sum"
        ],
        f"max {EXPERIENCE_TIMESPAN_TEXT}": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_max"
        ],
        f"min {EXPERIENCE_TIMESPAN_TEXT}": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_min"
        ],
        f"avg {EXPERIENCE_TIMESPAN_TEXT}": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_sum"
        ]
        / items_num
        if items_num > 0
        else 0,
        f"sum {EXPERIENCE_TIMESPAN_TEXT} backout": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_backout_sum"
        ],
        f"max {EXPERIENCE_TIMESPAN_TEXT} backout": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_backout_max"
        ],
        f"min {EXPERIENCE_TIMESPAN_TEXT} backout": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_backout_min"
        ],
        f"avg {EXPERIENCE_TIMESPAN_TEXT} backout": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_backout_sum"
        ]
        / items_num
        if items_num > 0
        else 0,
    }


class author_experience(object):
    name = "Author experience"

    def __call__(self, commit, **kwargs):
        return {
            "Author experience": commit["touched_prev_total_author_sum"],
            "Recent author experience": commit[
                f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_author_sum"
            ],
            "Author backouts": commit["touched_prev_total_author_backout_sum"],
            "Recent author backouts": commit[
                f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_author_backout_sum"
            ],
            "Author seniority": commit["seniority_author"] / 86400,
        }


class reviewer_experience(object):
    def __call__(self, commit, **kwargs):
        exps = get_exps("reviewer", commit)
        return {
            "Total reviewer experience": exps["sum"],
            "Maximum reviewer experience": exps["max"],
            "Minimum reviewer experience": exps["min"],
            "Average reviewer experience": exps["avg"],
            "Total reviewer backouts": exps["sum backout"],
            "Maximum reviewer backouts": exps["max backout"],
            "Minimum reviewer backouts": exps["min backout"],
            "Average reviewer backouts": exps["avg backout"],
            "Total recent reviewer experience": exps[f"sum {EXPERIENCE_TIMESPAN_TEXT}"],
            "Maximum recent reviewer experience": exps[
                f"max {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Minimum recent reviewer experience": exps[
                f"min {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Average recent reviewer experience": exps[
                f"avg {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Total recent reviewer backouts": exps[
                f"sum {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
            "Maximum recent reviewer backouts": exps[
                f"max {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
            "Minimum recent reviewer backouts": exps[
                f"min {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
            "Average recent reviewer backouts": exps[
                f"avg {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
        }


class reviewers_num(object):
    name = "# of reviewers"

    def __call__(self, commit, **kwargs):
        return len(commit["reviewers"])


class components(object):
    def __call__(self, commit, **kwargs):
        return commit["components"]


class components_modified_num(object):
    name = "# of components modified"

    def __call__(self, commit, **kwargs):
        return len(commit["components"])


class component_touched_prev(object):
    def __call__(self, commit, **kwargs):
        exps = get_exps("component", commit)
        return {
            "Total # of times these components have been touched before": exps["sum"],
            "Maximum # of times these components have been touched before": exps["max"],
            "Minimum # of times these components have been touched before": exps["min"],
            "Average # of times these components have been touched before": exps["avg"],
            "Total # of backouts in these components": exps["sum backout"],
            "Maximum # of backouts in these components": exps["max backout"],
            "Minimum # of backouts in these components": exps["min backout"],
            "Average # of backouts in these components": exps["avg backout"],
            "Total # of times these components have recently been touched": exps[
                f"sum {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Maximum # of times these components have recently been touched": exps[
                f"max {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Minimum # of times these components have recently been touched": exps[
                f"min {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Average # of times these components have recently been touched": exps[
                f"avg {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Total # of recent backouts in these components": exps[
                f"sum {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
            "Maximum # of recent backouts in these components": exps[
                f"max {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
            "Minimum # of recent backouts in these components": exps[
                f"min {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
            "Average # of recent backouts in these components": exps[
                f"avg {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
        }


class directories(object):
    def __call__(self, commit, **kwargs):
        return commit["directories"]


class directories_modified_num(object):
    name = "# of directories modified"

    def __call__(self, commit, **kwargs):
        return len(commit["directories"])


class directory_touched_prev(object):
    def __call__(self, commit, **kwargs):
        exps = get_exps("directory", commit)
        return {
            "Total # of times these directories have been touched before": exps["sum"],
            "Maximum # of times these directories have been touched before": exps[
                "max"
            ],
            "Minimum # of times these directories have been touched before": exps[
                "min"
            ],
            "Average # of times these directories have been touched before": exps[
                "avg"
            ],
            "Total # of backouts in these directories": exps["sum backout"],
            "Maximum # of backouts in these directories": exps["max backout"],
            "Minimum # of backouts in these directories": exps["min backout"],
            "Average # of backouts in these directories": exps["avg backout"],
            "Total # of times these directories have recently been touched": exps[
                f"sum {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Maximum # of times these directories have recently been touched": exps[
                f"max {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Minimum # of times these directories have recently been touched": exps[
                f"min {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Average # of times these directories have recently been touched": exps[
                f"avg {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Total # of recent backouts in these directories": exps[
                f"sum {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
            "Maximum # of recent backouts in these directories": exps[
                f"max {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
            "Minimum # of recent backouts in these directories": exps[
                f"min {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
            "Average # of recent backouts in these directories": exps[
                f"avg {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
        }


class files(object):
    def __init__(self, min_freq=0.0014):
        self.min_freq = min_freq

    def fit(self, commits):
        self.count = defaultdict(int)

        self.total_commits = 0

        for commit in commits:
            self.total_commits += 1

            for f in commit["files"]:
                self.count[f] += 1

        # We no longer need to store counts for files which have low frequency.
        to_del = set(
            f for f, c in self.count.items() if c / self.total_commits < self.min_freq
        )

        for f in to_del:
            del self.count[f]

    def __call__(self, commit, **kwargs):
        return [
            f
            for f in commit["files"]
            if (self.count[f] / self.total_commits) > self.min_freq
        ]


class file_touched_prev(object):
    def __call__(self, commit, **kwargs):
        exps = get_exps("file", commit)
        return {
            "Total # of times these files have been touched before": exps["sum"],
            "Maximum # of times these files have been touched before": exps["max"],
            "Minimum # of times these files have been touched before": exps["min"],
            "Average # of times these files have been touched before": exps["avg"],
            "Total # of backouts in these files": exps["sum backout"],
            "Maximum # of backouts in these files": exps["max backout"],
            "Minimum # of backouts in these files": exps["min backout"],
            "Average # of backouts in these files": exps["avg backout"],
            "Total # of times these files have recently been touched": exps[
                f"sum {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Maximum # of times these files have recently been touched": exps[
                f"max {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Minimum # of times these files have recently been touched": exps[
                f"min {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Average # of times these files have recently been touched": exps[
                f"avg {EXPERIENCE_TIMESPAN_TEXT}"
            ],
            "Total # of recent backouts in these files": exps[
                f"sum {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
            "Maximum # of recent backouts in these files": exps[
                f"max {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
            "Minimum # of recent backouts in these files": exps[
                f"min {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
            "Average # of recent backouts in these files": exps[
                f"avg {EXPERIENCE_TIMESPAN_TEXT} backout"
            ],
        }


class types(object):
    name = "file types"

    def __call__(self, commit, **kwargs):
        return commit["types"]


def merge_metrics(objects):
    metrics = {}

    for metric in repository.METRIC_NAMES:
        metrics.update(
            {
                f"{metric}_avg": sum(obj["metrics"][f"{metric}_avg"] for obj in objects)
                / len(objects),
                f"{metric}_max": max(
                    (obj["metrics"][f"{metric}_max"] for obj in objects)
                ),
                f"{metric}_min": min(
                    (obj["metrics"][f"{metric}_min"] for obj in objects)
                ),
                f"{metric}_total": sum(
                    obj["metrics"][f"{metric}_total"] for obj in objects
                ),
            }
        )

    return metrics


def merge_commits(commits: Sequence[repository.CommitDict]) -> repository.CommitDict:
    return repository.CommitDict(
        {
            "nodes": list(commit["node"] for commit in commits),
            "pushdate": commits[0]["pushdate"],
            "types": list(set(sum((commit["types"] for commit in commits), []))),
            "files": list(set(sum((commit["files"] for commit in commits), []))),
            "directories": list(
                set(sum((commit["directories"] for commit in commits), []))
            ),
            "components": list(
                set(sum((commit["components"] for commit in commits), []))
            ),
            "reviewers": list(
                set(sum((commit["reviewers"] for commit in commits), []))
            ),
            "source_code_files_modified_num": sum(
                commit["source_code_files_modified_num"] for commit in commits
            ),
            "other_files_modified_num": sum(
                commit["other_files_modified_num"] for commit in commits
            ),
            "test_files_modified_num": sum(
                commit["test_files_modified_num"] for commit in commits
            ),
            "total_source_code_file_size": sum(
                commit["total_source_code_file_size"] for commit in commits
            ),
            "average_source_code_file_size": sum(
                commit["total_source_code_file_size"] for commit in commits
            )
            / len(commits),
            "maximum_source_code_file_size": max(
                commit["maximum_source_code_file_size"] for commit in commits
            ),
            "minimum_source_code_file_size": min(
                commit["minimum_source_code_file_size"] for commit in commits
            ),
            "total_other_file_size": sum(
                commit["total_other_file_size"] for commit in commits
            ),
            "average_other_file_size": sum(
                commit["total_other_file_size"] for commit in commits
            )
            / len(commits),
            "maximum_other_file_size": max(
                commit["maximum_other_file_size"] for commit in commits
            ),
            "minimum_other_file_size": min(
                commit["minimum_other_file_size"] for commit in commits
            ),
            "total_test_file_size": sum(
                commit["total_test_file_size"] for commit in commits
            ),
            "average_test_file_size": sum(
                commit["total_test_file_size"] for commit in commits
            )
            / len(commits),
            "maximum_test_file_size": max(
                commit["maximum_test_file_size"] for commit in commits
            ),
            "minimum_test_file_size": min(
                commit["minimum_test_file_size"] for commit in commits
            ),
            "source_code_added": sum(commit["source_code_added"] for commit in commits),
            "other_added": sum(commit["other_added"] for commit in commits),
            "test_added": sum(commit["test_added"] for commit in commits),
            "source_code_deleted": sum(
                commit["source_code_deleted"] for commit in commits
            ),
            "other_deleted": sum(commit["other_deleted"] for commit in commits),
            "test_deleted": sum(commit["test_deleted"] for commit in commits),
            "metrics": merge_metrics(commits),
        }
    )


class CommitExtractor(BaseEstimator, TransformerMixin):
    def __init__(self, feature_extractors, cleanup_functions):
        assert len(set(type(fe) for fe in feature_extractors)) == len(
            feature_extractors
        ), "Duplicate Feature Extractors"
        self.feature_extractors = feature_extractors

        assert len(set(type(cf) for cf in cleanup_functions)) == len(
            cleanup_functions
        ), "Duplicate Cleanup Functions"
        self.cleanup_functions = cleanup_functions

    def fit(self, x, y=None):
        for feature in self.feature_extractors:
            if hasattr(feature, "fit"):
                feature.fit(x())

        return self

    def transform(self, commits):
        results = []

        for commit in commits():
            data = {}

            for feature_extractor in self.feature_extractors:
                if "bug_features" in feature_extractor.__module__:
                    if not commit["bug"]:
                        continue

                    res = feature_extractor(commit["bug"])
                elif "test_scheduling_features" in feature_extractor.__module__:
                    res = feature_extractor(commit["test_job"], commit=commit)
                else:
                    res = feature_extractor(commit)

                if res is None:
                    continue

                if hasattr(feature_extractor, "name"):
                    feature_extractor_name = feature_extractor.name
                else:
                    feature_extractor_name = feature_extractor.__class__.__name__

                if isinstance(res, dict):
                    for key, value in res.items():
                        data[sys.intern(key)] = value
                    continue

                if isinstance(res, list):
                    for item in res:
                        data[sys.intern(f"{item} in {feature_extractor_name}")] = "True"
                    continue

                if isinstance(res, bool):
                    res = str(res)

                data[sys.intern(feature_extractor_name)] = res

            # TODO: Try simply using all possible fields instead of extracting features manually.

            result = {"data": data}
            if "desc" in commit:
                for cleanup_function in self.cleanup_functions:
                    result["desc"] = cleanup_function(commit["desc"])

            results.append(result)

        return pd.DataFrame(results)
