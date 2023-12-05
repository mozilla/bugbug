# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
import sys
from typing import Sequence

import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

from bugbug import repository

EXPERIENCE_TIMESPAN = 90
EXPERIENCE_TIMESPAN_TEXT = f"{EXPERIENCE_TIMESPAN}_days"


class SourceCodeFilesModifiedNum(object):
    name = "# of modified code files"

    def __call__(self, commit, **kwargs):
        return commit["source_code_files_modified_num"]


class OtherFilesModifiedNum(object):
    name = "# of modified non-code files"

    def __call__(self, commit, **kwargs):
        return commit["other_files_modified_num"]


class TestFilesModifiedNum(object):
    name = "# of modified test files"

    def __call__(self, commit, **kwargs):
        return commit["test_files_modified_num"]


class SourceCodeFileSize(object):
    def __call__(self, commit, **kwargs):
        return {
            "Total code files size": commit["total_source_code_file_size"],
            "Average code files size": commit["average_source_code_file_size"],
            "Maximum code files size": commit["maximum_source_code_file_size"],
            "Minimum code files size": commit["minimum_source_code_file_size"],
        }


class OtherFileSize(object):
    def __call__(self, commit, **kwargs):
        return {
            "Total non-code files size": commit["total_other_file_size"],
            "Average non-code files size": commit["average_other_file_size"],
            "Maximum non-code files size": commit["maximum_other_file_size"],
            "Minimum non-code files size": commit["minimum_other_file_size"],
        }


class TestFileSize(object):
    def __call__(self, commit, **kwargs):
        return {
            "Total test files size": commit["total_test_file_size"],
            "Average test files size": commit["average_test_file_size"],
            "Maximum test files size": commit["maximum_test_file_size"],
            "Minimum test files size": commit["minimum_test_file_size"],
        }


class SourceCodeAdded(object):
    name = "# of code lines added"

    def __call__(self, commit, **kwargs):
        return commit["source_code_added"]


class OtherAdded(object):
    name = "# of non-code lines added"

    def __call__(self, commit, **kwargs):
        return commit["other_added"]


class TestAdded(object):
    name = "# of lines added in tests"

    def __call__(self, commit, **kwargs):
        return commit["test_added"]


class SourceCodeDeleted(object):
    name = "# of code lines deleted"

    def __call__(self, commit, **kwargs):
        return commit["source_code_deleted"]


class OtherDeleted(object):
    name = "# of non-code lines deleted"

    def __call__(self, commit, **kwargs):
        return commit["other_deleted"]


class TestDeleted(object):
    name = "# of lines deleted in tests"

    def __call__(self, commit, **kwargs):
        return commit["test_deleted"]


class FunctionsTouchedNum(object):
    name = "# of functions touched"

    def __call__(self, commit, **kwargs):
        return sum(1 for f_group in commit["functions"].values() for f in f_group)


class FunctionsTouchedSize(object):
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


class SourceCodeFileMetrics(object):
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
            "Average file length": commit["metrics"]["halstead_length_avg"],
            "Average calculated estimated file length": commit["metrics"][
                "halstead_estimated_program_length_avg"
            ],
            "Average file purity ratio": commit["metrics"]["halstead_purity_ratio_avg"],
            "Average file vocabulary": commit["metrics"]["halstead_vocabulary_avg"],
            "Average file volume": commit["metrics"]["halstead_volume_avg"],
            "Average file estimated difficulty": commit["metrics"][
                "halstead_difficulty_avg"
            ],
            "Average file estimated level of difficulty": commit["metrics"][
                "halstead_level_avg"
            ],
            "Average file estimated effort": commit["metrics"]["halstead_effort_avg"],
            "Average file estimated time": commit["metrics"]["halstead_time_avg"],
            "Average file estimated number of delivered bugs": commit["metrics"][
                "halstead_bugs_avg"
            ],
            "Average file number of functions": commit["metrics"]["functions_avg"],
            "Average file number of closures": commit["metrics"]["closures_avg"],
            "Average file number of source loc": commit["metrics"]["sloc_avg"],
            "Average file number of instruction loc": commit["metrics"]["ploc_avg"],
            "Average file number of logical loc": commit["metrics"]["lloc_avg"],
            "Average file number of comment loc": commit["metrics"]["cloc_avg"],
            "Average file blank": commit["metrics"]["blank_avg"],
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
            "Maximum file length": commit["metrics"]["halstead_length_max"],
            "Maximum calculated estimated file length": commit["metrics"][
                "halstead_estimated_program_length_max"
            ],
            "Maximum file purity ratio": commit["metrics"]["halstead_purity_ratio_max"],
            "Maximum file vocabulary": commit["metrics"]["halstead_vocabulary_max"],
            "Maximum file volume": commit["metrics"]["halstead_volume_max"],
            "Maximum file estimated difficulty": commit["metrics"][
                "halstead_difficulty_max"
            ],
            "Maximum file estimated level of difficulty": commit["metrics"][
                "halstead_level_max"
            ],
            "Maximum file estimated effort": commit["metrics"]["halstead_effort_max"],
            "Maximum file estimated time": commit["metrics"]["halstead_time_max"],
            "Maximum file estimated number of delivered bugs": commit["metrics"][
                "halstead_bugs_max"
            ],
            "Maximum file number of functions": commit["metrics"]["functions_max"],
            "Maximum file number of closures": commit["metrics"]["closures_max"],
            "Maximum file number of source loc": commit["metrics"]["sloc_max"],
            "Maximum file number of instruction loc": commit["metrics"]["ploc_max"],
            "Maximum file number of logical loc": commit["metrics"]["lloc_max"],
            "Maximum file number of comment loc": commit["metrics"]["cloc_max"],
            "Maximum file blank": commit["metrics"]["blank_max"],
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
            "Minimum file length": commit["metrics"]["halstead_length_min"],
            "Minimum calculated estimated file length": commit["metrics"][
                "halstead_estimated_program_length_min"
            ],
            "Minimum file purity ratio": commit["metrics"]["halstead_purity_ratio_min"],
            "Minimum file vocabulary": commit["metrics"]["halstead_vocabulary_min"],
            "Minimum file volume": commit["metrics"]["halstead_volume_min"],
            "Minimum file estimated difficulty": commit["metrics"][
                "halstead_difficulty_min"
            ],
            "Minimum file estimated level of difficulty": commit["metrics"][
                "halstead_level_min"
            ],
            "Minimum file estimated effort": commit["metrics"]["halstead_effort_min"],
            "Minimum file estimated time": commit["metrics"]["halstead_time_min"],
            "Minimum file estimated number of delivered bugs": commit["metrics"][
                "halstead_bugs_min"
            ],
            "Minimum file number of functions": commit["metrics"]["functions_min"],
            "Minimum file number of closures": commit["metrics"]["closures_min"],
            "Minimum file number of source loc": commit["metrics"]["sloc_min"],
            "Minimum file number of instruction loc": commit["metrics"]["ploc_min"],
            "Minimum file number of logical loc": commit["metrics"]["lloc_min"],
            "Minimum file number of comment loc": commit["metrics"]["cloc_min"],
            "Minimum file blank": commit["metrics"]["blank_min"],
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
            "Total file length": commit["metrics"]["halstead_length_total"],
            "Total calculated estimated file length": commit["metrics"][
                "halstead_estimated_program_length_total"
            ],
            "Total file purity ratio": commit["metrics"]["halstead_purity_ratio_total"],
            "Total file vocabulary": commit["metrics"]["halstead_vocabulary_total"],
            "Total file volume": commit["metrics"]["halstead_volume_total"],
            "Total file estimated difficulty": commit["metrics"][
                "halstead_difficulty_total"
            ],
            "Total file estimated level of difficulty": commit["metrics"][
                "halstead_level_total"
            ],
            "Total file estimated effort": commit["metrics"]["halstead_effort_total"],
            "Total file estimated time": commit["metrics"]["halstead_time_total"],
            "Total file estimated number of delivered bugs": commit["metrics"][
                "halstead_bugs_total"
            ],
            "Total file number of functions": commit["metrics"]["functions_total"],
            "Total file number of closures": commit["metrics"]["closures_total"],
            "Total file number of source loc": commit["metrics"]["sloc_total"],
            "Total file number of instruction loc": commit["metrics"]["ploc_total"],
            "Total file number of logical loc": commit["metrics"]["lloc_total"],
            "Total file number of comment loc": commit["metrics"]["cloc_total"],
            "Total file blank": commit["metrics"]["blank_total"],
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


class SourceCodeFunctionMetrics(object):
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
            "Average function length": merged_metrics["halstead_length_avg"],
            "Average calculated estimated function length": merged_metrics[
                "halstead_estimated_program_length_avg"
            ],
            "Average function purity ratio": merged_metrics[
                "halstead_purity_ratio_avg"
            ],
            "Average function vocabulary": merged_metrics["halstead_vocabulary_avg"],
            "Average function volume": merged_metrics["halstead_volume_avg"],
            "Average function estimated difficulty": merged_metrics[
                "halstead_difficulty_avg"
            ],
            "Average function estimated level of difficulty": merged_metrics[
                "halstead_level_avg"
            ],
            "Average function estimated effort": merged_metrics["halstead_effort_avg"],
            "Average function estimated time": merged_metrics["halstead_time_avg"],
            "Average function estimated number of delivered bugs": merged_metrics[
                "halstead_bugs_avg"
            ],
            "Average function number of functions": merged_metrics["functions_avg"],
            "Average function number of closures": merged_metrics["closures_avg"],
            "Average function number of source loc": merged_metrics["sloc_avg"],
            "Average function number of instruction loc": merged_metrics["ploc_avg"],
            "Average function number of logical loc": merged_metrics["lloc_avg"],
            "Average function number of comment loc": merged_metrics["cloc_avg"],
            "Average function blank": merged_metrics["blank_avg"],
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
            "Maximum function length": merged_metrics["halstead_length_max"],
            "Maximum calculated estimated function length": merged_metrics[
                "halstead_estimated_program_length_max"
            ],
            "Maximum function purity ratio": merged_metrics[
                "halstead_purity_ratio_max"
            ],
            "Maximum function vocabulary": merged_metrics["halstead_vocabulary_max"],
            "Maximum function volume": merged_metrics["halstead_volume_max"],
            "Maximum function estimated difficulty": merged_metrics[
                "halstead_difficulty_max"
            ],
            "Maximum function estimated level of difficulty": merged_metrics[
                "halstead_level_max"
            ],
            "Maximum function estimated effort": merged_metrics["halstead_effort_max"],
            "Maximum function estimated time": merged_metrics["halstead_time_max"],
            "Maximum function estimated number of delivered bugs": merged_metrics[
                "halstead_bugs_max"
            ],
            "Maximum function number of functions": merged_metrics["functions_max"],
            "Maximum function number of closures": merged_metrics["closures_max"],
            "Maximum function number of source loc": merged_metrics["sloc_max"],
            "Maximum function number of instruction loc": merged_metrics["ploc_max"],
            "Maximum function number of logical loc": merged_metrics["lloc_max"],
            "Maximum function number of comment loc": merged_metrics["cloc_max"],
            "Maximum function blank": merged_metrics["blank_max"],
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
            "Minimum function length": merged_metrics["halstead_length_min"],
            "Minimum calculated estimated function length": merged_metrics[
                "halstead_estimated_program_length_min"
            ],
            "Minimum function purity ratio": merged_metrics[
                "halstead_purity_ratio_min"
            ],
            "Minimum function vocabulary": merged_metrics["halstead_vocabulary_min"],
            "Minimum function volume": merged_metrics["halstead_volume_min"],
            "Minimum function estimated difficulty": merged_metrics[
                "halstead_difficulty_min"
            ],
            "Minimum function estimated level of difficulty": merged_metrics[
                "halstead_level_min"
            ],
            "Minimum function estimated effort": merged_metrics["halstead_effort_min"],
            "Minimum function estimated time": merged_metrics["halstead_time_min"],
            "Minimum function estimated number of delivered bugs": merged_metrics[
                "halstead_bugs_min"
            ],
            "Minimum function number of functions": merged_metrics["functions_min"],
            "Minimum function number of closures": merged_metrics["closures_min"],
            "Minimum function number of source loc": merged_metrics["sloc_min"],
            "Minimum function number of instruction loc": merged_metrics["ploc_min"],
            "Minimum function number of logical loc": merged_metrics["lloc_min"],
            "Minimum function number of comment loc": merged_metrics["cloc_min"],
            "Minimum function blank": merged_metrics["blank_min"],
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
            "Total function length": merged_metrics["halstead_length_total"],
            "Total calculated estimated function length": merged_metrics[
                "halstead_estimated_program_length_total"
            ],
            "Total function purity ratio": merged_metrics[
                "halstead_purity_ratio_total"
            ],
            "Total function vocabulary": merged_metrics["halstead_vocabulary_total"],
            "Total function volume": merged_metrics["halstead_volume_total"],
            "Total function estimated difficulty": merged_metrics[
                "halstead_difficulty_total"
            ],
            "Total function estimated level of difficulty": merged_metrics[
                "halstead_level_total"
            ],
            "Total function estimated effort": merged_metrics["halstead_effort_total"],
            "Total function estimated time": merged_metrics["halstead_time_total"],
            "Total function estimated number of delivered bugs": merged_metrics[
                "halstead_bugs_total"
            ],
            "Total function number of functions": merged_metrics["functions_total"],
            "Total function number of closures": merged_metrics["closures_total"],
            "Total function number of source loc": merged_metrics["sloc_total"],
            "Total function number of instruction loc": merged_metrics["ploc_total"],
            "Total function number of logical loc": merged_metrics["lloc_total"],
            "Total function number of comment loc": merged_metrics["cloc_total"],
            "Total function blank": merged_metrics["blank_total"],
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


class SourceCodeMetricsDiff(object):
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
            "Diff in length": commit["metrics_diff"]["halstead_length_total"],
            "Diff in calculated estimated length": commit["metrics_diff"][
                "halstead_estimated_program_length_total"
            ],
            "Diff in purity ratio": commit["metrics_diff"][
                "halstead_purity_ratio_total"
            ],
            "Diff in vocabulary": commit["metrics_diff"]["halstead_vocabulary_total"],
            "Diff in volume": commit["metrics_diff"]["halstead_volume_total"],
            "Diff in estimated difficulty": commit["metrics_diff"][
                "halstead_difficulty_total"
            ],
            "Diff in estimated level of difficulty": commit["metrics_diff"][
                "halstead_level_total"
            ],
            "Diff in estimated effort": commit["metrics_diff"]["halstead_effort_total"],
            "Diff in estimated time": commit["metrics_diff"]["halstead_time_total"],
            "Diff in estimated number of delivered bugs": commit["metrics_diff"][
                "halstead_bugs_total"
            ],
            "Diff in number of functions": commit["metrics_diff"]["functions_total"],
            "Diff in number of closures": commit["metrics_diff"]["closures_total"],
            "Diff in number of source loc": commit["metrics_diff"]["sloc_total"],
            "Diff in number of instruction loc": commit["metrics_diff"]["ploc_total"],
            "Diff in number of logical loc": commit["metrics_diff"]["lloc_total"],
            "Diff in number of comment loc": commit["metrics_diff"]["cloc_total"],
            "Diff in blank": commit["metrics_diff"]["blank_total"],
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


class AuthorExperience(object):
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


class ReviewerExperience(object):
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


class ReviewersNum(object):
    name = "# of reviewers"

    def __call__(self, commit, **kwargs):
        return len(commit["reviewers"])


class Components(object):
    def __call__(self, commit, **kwargs):
        return commit["components"]


class ComponentsModifiedNum(object):
    name = "# of components modified"

    def __call__(self, commit, **kwargs):
        return len(commit["components"])


class ComponentTouchedPrev(object):
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


class Directories(object):
    def __call__(self, commit, **kwargs):
        return commit["directories"]


class DirectoriesModifiedNum(object):
    name = "# of directories modified"

    def __call__(self, commit, **kwargs):
        return len(commit["directories"])


class DirectoryTouchedPrev(object):
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


class Files(object):
    name = "files"

    def __call__(self, commit, **kwargs):
        return commit["files"]


def _pass_through_tokenizer(doc):
    return doc


class FileTouchedPrev(object):
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


class Types(object):
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
            result = {"data": data}

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

                # FIXME: This is a workaround to pass the value to the
                # union transformer independently. This will be dropped when we
                # resolve https://github.com/mozilla/bugbug/issues/3876
                if isinstance(feature_extractor, Files):
                    result[sys.intern(feature_extractor_name)] = res
                    continue

                if isinstance(res, dict):
                    for key, value in res.items():
                        data[sys.intern(key)] = value
                    continue

                if isinstance(res, list):
                    for item in res:
                        data[sys.intern(f"{item} in {feature_extractor_name}")] = True
                    continue

                data[sys.intern(feature_extractor_name)] = res

            if "desc" in commit:
                for cleanup_function in self.cleanup_functions:
                    result["desc"] = cleanup_function(commit["desc"])

            results.append(result)

        return pd.DataFrame(results)
