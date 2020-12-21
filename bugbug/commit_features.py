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
            f[2] - f[1] + 1 for f_group in commit["functions"].values() for f in f_group
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
            "Average cyclomatic": commit["average_cyclomatic"],
            "Average number of unique operands": commit["average_halstead_n2"],
            "Average number of operands": commit["average_halstead_N2"],
            "Average number of unique operators": commit["average_halstead_n1"],
            "Average number of operators": commit["average_halstead_N1"],
            "Average number of source loc": commit["average_source_loc"],
            "Average number of instruction loc": commit["average_instruction_loc"],
            "Average number of logical loc": commit["average_logical_loc"],
            "Average number of comment loc": commit["average_comment_loc"],
            "Average number of function arguments": commit["average_nargs"],
            "Average number of function exit points": commit["average_nexits"],
            "Maximum cyclomatic": commit["maximum_cyclomatic"],
            "Maximum number of unique operands": commit["maximum_halstead_n2"],
            "Maximum number of operands": commit["maximum_halstead_N2"],
            "Maximum number of unique operators": commit["maximum_halstead_n1"],
            "Maximum number of operators": commit["maximum_halstead_N1"],
            "Maximum number of source loc": commit["maximum_source_loc"],
            "Maximum number of instruction loc": commit["maximum_instruction_loc"],
            "Maximum number of logical loc": commit["maximum_logical_loc"],
            "Maximum number of comment loc": commit["maximum_comment_loc"],
            "Maximum number of function arguments": commit["maximum_nargs"],
            "Maximum number of function exit points": commit["maximum_nexits"],
            "Minimum cyclomatic": commit["minimum_cyclomatic"],
            "Minimum number of unique operands": commit["minimum_halstead_n2"],
            "Minimum number of operands": commit["minimum_halstead_N2"],
            "Minimum number of unique operators": commit["minimum_halstead_n1"],
            "Minimum number of operators": commit["minimum_halstead_N1"],
            "Minimum number of source loc": commit["minimum_source_loc"],
            "Minimum number of instruction loc": commit["minimum_instruction_loc"],
            "Minimum number of logical loc": commit["minimum_logical_loc"],
            "Minimum number of comment loc": commit["minimum_comment_loc"],
            "Minimum number of function arguments": commit["minimum_nargs"],
            "Minimum number of function exit points": commit["minimum_nexits"],
            "Total of number of operands": commit["total_halstead_N2"],
            "Total of number of unique operators": commit["total_halstead_n1"],
            "Total number of operators": commit["total_halstead_N1"],
            "Total number of source loc": commit["total_source_loc"],
            "Total number of instruction loc": commit["total_instruction_loc"],
            "Total number of logical loc": commit["total_logical_loc"],
            "Total number of comment loc": commit["total_comment_loc"],
            "Total number of function arguments": commit["total_nargs"],
            "Total number of function exit points": commit["total_nexits"],
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
            "average_cyclomatic": sum(
                commit["average_cyclomatic"] for commit in commits
            )
            / len(commits),
            "average_halstead_n2": sum(
                commit["average_halstead_n2"] for commit in commits
            )
            / len(commits),
            "average_halstead_N2": sum(
                commit["average_halstead_N2"] for commit in commits
            )
            / len(commits),
            "average_halstead_n1": sum(
                commit["average_halstead_n1"] for commit in commits
            )
            / len(commits),
            "average_halstead_N1": sum(
                commit["average_halstead_N1"] for commit in commits
            )
            / len(commits),
            "average_source_loc": sum(
                commit["average_source_loc"] for commit in commits
            )
            / len(commits),
            "average_instruction_loc": sum(
                commit["average_instruction_loc"] for commit in commits
            )
            / len(commits),
            "average_logical_loc": sum(
                commit["average_logical_loc"] for commit in commits
            )
            / len(commits),
            "average_comment_loc": sum(
                commit["average_comment_loc"] for commit in commits
            )
            / len(commits),
            "average_nargs": sum(commit["average_nargs"] for commit in commits)
            / len(commits),
            "average_nexits": sum(commit["average_nexits"] for commit in commits)
            / len(commits),
            "maximum_cyclomatic": max(
                commit["maximum_cyclomatic"] for commit in commits
            ),
            "maximum_halstead_n2": max(
                commit["maximum_halstead_n2"] for commit in commits
            ),
            "maximum_halstead_N2": max(
                commit["maximum_halstead_N2"] for commit in commits
            ),
            "maximum_halstead_n1": max(
                commit["maximum_halstead_n1"] for commit in commits
            ),
            "maximum_halstead_N1": max(
                commit["maximum_halstead_N1"] for commit in commits
            ),
            "maximum_source_loc": max(
                commit["maximum_source_loc"] for commit in commits
            ),
            "maximum_instruction_loc": max(
                commit["maximum_instruction_loc"] for commit in commits
            ),
            "maximum_logical_loc": max(
                commit["maximum_logical_loc"] for commit in commits
            ),
            "maximum_comment_loc": max(
                commit["maximum_comment_loc"] for commit in commits
            ),
            "maximum_nargs": max(commit["maximum_nargs"] for commit in commits),
            "maximum_nexits": max(commit["maximum_nexits"] for commit in commits),
            "minimum_cyclomatic": min(
                commit["minimum_cyclomatic"] for commit in commits
            ),
            "minimum_halstead_n2": min(
                commit["minimum_halstead_n2"] for commit in commits
            ),
            "minimum_halstead_N2": min(
                commit["minimum_halstead_N2"] for commit in commits
            ),
            "minimum_halstead_n1": min(
                commit["minimum_halstead_n1"] for commit in commits
            ),
            "minimum_halstead_N1": min(
                commit["minimum_halstead_N1"] for commit in commits
            ),
            "minimum_source_loc": min(
                commit["minimum_source_loc"] for commit in commits
            ),
            "minimum_instruction_loc": min(
                commit["minimum_instruction_loc"] for commit in commits
            ),
            "minimum_logical_loc": min(
                commit["minimum_logical_loc"] for commit in commits
            ),
            "minimum_comment_loc": min(
                commit["minimum_comment_loc"] for commit in commits
            ),
            "minimum_nargs": min(commit["minimum_nargs"] for commit in commits),
            "minimum_nexits": min(commit["minimum_nexits"] for commit in commits),
            "total_halstead_n2": sum(commit["total_halstead_n2"] for commit in commits),
            "total_halstead_N2": sum(commit["total_halstead_N2"] for commit in commits),
            "total_halstead_n1": sum(commit["total_halstead_n1"] for commit in commits),
            "total_halstead_N1": sum(commit["total_halstead_N1"] for commit in commits),
            "total_source_loc": sum(commit["total_source_loc"] for commit in commits),
            "total_instruction_loc": sum(
                commit["total_instruction_loc"] for commit in commits
            ),
            "total_logical_loc": sum(commit["total_logical_loc"] for commit in commits),
            "total_comment_loc": sum(commit["total_comment_loc"] for commit in commits),
            "total_nargs": sum(commit["total_nargs"] for commit in commits),
            "total_nexits": sum(commit["total_nexits"] for commit in commits),
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
