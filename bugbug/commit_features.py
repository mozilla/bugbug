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


class author_experience_90_days(object):
    def __call__(self, commit, **kwargs):
        return commit["author_experience_90_days"]


def get_exps(exp_type, commit):
    suffix = "experience" if exp_type == "reviewer" else "touched_prev"

    val_total = commit[f"{exp_type}_{suffix}"]
    val_timespan = commit[f"{exp_type}_{suffix}_{EXPERIENCE_TIMESPAN_TEXT}"]

    items_key = f"{exp_type}s" if exp_type != "directory" else "directories"
    items_num = len(commit[items_key])

    return {
        "num": items_num,
        "sum": val_total["sum"],
        "max": val_total["max"],
        "min": val_total["min"],
        "avg": val_total["sum"] / items_num if items_num > 0 else 0,
        f"sum_{EXPERIENCE_TIMESPAN_TEXT}": val_timespan["sum"],
        f"max_{EXPERIENCE_TIMESPAN_TEXT}": val_timespan["max"],
        f"min_{EXPERIENCE_TIMESPAN_TEXT}": val_timespan["min"],
        f"avg_{EXPERIENCE_TIMESPAN_TEXT}": val_timespan["sum"] / items_num
        if items_num > 0
        else 0,
    }


class author_experience(object):
    def __call__(self, commit, **kwargs):
        return {
            "total": commit["author_experience"],
            EXPERIENCE_TIMESPAN_TEXT: commit[
                f"author_experience_{EXPERIENCE_TIMESPAN_TEXT}"
            ],
        }


class reviewer_experience(object):
    def __call__(self, commit, **kwargs):
        return get_exps("reviewer", commit)


class components(object):
    def __call__(self, commit, **kwargs):
        return commit["components"]


class component_touched_prev(object):
    def __call__(self, commit, **kwargs):
        return get_exps("component", commit)


class directories(object):
    def __call__(self, commit, **kwargs):
        return commit["directories"]


class directory_touched_prev(object):
    def __call__(self, commit, **kwargs):
        return get_exps("directory", commit)


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
                res = feature_extractor(commit)

                feature_extractor_name = feature_extractor.__class__.__name__

                if res is None:
                    continue

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
