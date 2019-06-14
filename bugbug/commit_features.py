# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

EXPERIENCE_TIMESPAN = 90
EXPERIENCE_TIMESPAN_TEXT = f"{EXPERIENCE_TIMESPAN}_days"


class files_modified_num(object):
    def __call__(self, commit, **kwargs):
        return commit["files_modified_num"]


class file_size(object):
    def __call__(self, commit, **kwargs):
        return {
            "sum": commit["total_file_size"],
            "avg": commit["average_file_size"],
            "max": commit["maximum_file_size"],
            "min": commit["minimum_file_size"],
        }


class added(object):
    def __call__(self, commit, **kwargs):
        return commit["added"]


class test_added(object):
    def __call__(self, commit, **kwargs):
        return commit["test_added"]


class deleted(object):
    def __call__(self, commit, **kwargs):
        return commit["deleted"]


class test_deleted(object):
    def __call__(self, commit, **kwargs):
        return commit["test_deleted"]


def get_exps(exp_type, commit):
    items_key = f"{exp_type}s" if exp_type != "directory" else "directories"
    items_num = len(commit[items_key])

    return {
        "num": items_num,
        "sum": commit[f"touched_prev_total_{exp_type}_sum"],
        "max": commit[f"touched_prev_total_{exp_type}_max"],
        "min": commit[f"touched_prev_total_{exp_type}_min"],
        "avg": commit[f"touched_prev_total_{exp_type}_sum"] / items_num
        if items_num > 0
        else 0,
        "sum_backout": commit[f"touched_prev_total_{exp_type}_backout_sum"],
        "max_backout": commit[f"touched_prev_total_{exp_type}_backout_max"],
        "min_backout": commit[f"touched_prev_total_{exp_type}_backout_min"],
        "avg_backout": commit[f"touched_prev_total_{exp_type}_backout_sum"] / items_num
        if items_num > 0
        else 0,
        f"sum_{EXPERIENCE_TIMESPAN_TEXT}": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_sum"
        ],
        f"max_{EXPERIENCE_TIMESPAN_TEXT}": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_max"
        ],
        f"min_{EXPERIENCE_TIMESPAN_TEXT}": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_min"
        ],
        f"avg_{EXPERIENCE_TIMESPAN_TEXT}": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_sum"
        ]
        / items_num
        if items_num > 0
        else 0,
        f"sum_{EXPERIENCE_TIMESPAN_TEXT}_backout": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_backout_sum"
        ],
        f"max_{EXPERIENCE_TIMESPAN_TEXT}_backout": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_backout_max"
        ],
        f"min_{EXPERIENCE_TIMESPAN_TEXT}_backout": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_backout_min"
        ],
        f"avg_{EXPERIENCE_TIMESPAN_TEXT}_backout": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_backout_sum"
        ]
        / items_num
        if items_num > 0
        else 0,
    }


class author_experience(object):
    def __call__(self, commit, **kwargs):
        return {
            "total": commit["touched_prev_total_author_sum"],
            EXPERIENCE_TIMESPAN_TEXT: commit[
                f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_author_sum"
            ],
            "total_backout": commit["touched_prev_total_author_backout_sum"],
            f"{EXPERIENCE_TIMESPAN_TEXT}_backout": commit[
                f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_author_backout_sum"
            ],
            "seniority_author": commit["seniority_author"] / 86400,
        }


class reviewer_experience(object):
    def __call__(self, commit, **kwargs):
        return get_exps("reviewer", commit)


class components(object):
    def __call__(self, commit, **kwargs):
        return commit["components"]


class components_modified_num(object):
    def __call__(self, commit, **kwargs):
        return len(commit["components"])


class component_touched_prev(object):
    def __call__(self, commit, **kwargs):
        return get_exps("component", commit)


class directories(object):
    def __call__(self, commit, **kwargs):
        return commit["directories"]


class directories_modified_num(object):
    def __call__(self, commit, **kwargs):
        return len(commit["directories"])


class directory_touched_prev(object):
    def __call__(self, commit, **kwargs):
        return get_exps("directory", commit)


class files(object):
    def __call__(self, commit, **kwargs):
        return commit["files"]


class file_touched_prev(object):
    def __call__(self, commit, **kwargs):
        return get_exps("file", commit)


class types(object):
    def __call__(self, commit, **kwargs):
        return commit["types"]


class CommitExtractor(BaseEstimator, TransformerMixin):
    def __init__(self, feature_extractors, cleanup_functions):
        self.feature_extractors = feature_extractors
        self.cleanup_functions = cleanup_functions

    def fit(self, x, y=None):
        return self

    def transform(self, commits):
        results = []

        for commit in commits:
            data = {}

            for feature_extractor in self.feature_extractors:
                if "bug_features" in feature_extractor.__module__:
                    if not commit["bug"]:
                        continue

                    res = feature_extractor(commit["bug"])
                else:
                    res = feature_extractor(commit)

                if res is None:
                    continue

                feature_extractor_name = feature_extractor.__class__.__name__

                if isinstance(res, dict):
                    for key, value in res.items():
                        data[f"{feature_extractor_name}_{key}"] = value
                    continue

                if isinstance(res, list):
                    for item in res:
                        data[f"{feature_extractor_name}-{item}"] = "True"
                    continue

                if isinstance(res, bool):
                    res = str(res)

                data[feature_extractor_name] = res

            # TODO: Try simply using all possible fields instead of extracting features manually.

            for cleanup_function in self.cleanup_functions:
                commit["desc"] = cleanup_function(commit["desc"])

            result = {"data": data, "desc": commit["desc"]}

            results.append(result)

        return pd.DataFrame(results)
