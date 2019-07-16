# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
from collections import defaultdict

import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

EXPERIENCE_TIMESPAN = 90
EXPERIENCE_TIMESPAN_TEXT = f"{EXPERIENCE_TIMESPAN}_days"
EXPERIENCE_TIMESPAN_TEXT_USER = f"{EXPERIENCE_TIMESPAN} days"


class files_modified_num(object):
    name = "# of modified files"

    def __call__(self, commit, **kwargs):
        return commit["files_modified_num"]


class file_size(object):
    name = "File size"

    def __call__(self, commit, **kwargs):
        return {
            "sum": commit["total_file_size"],
            "avg": commit["average_file_size"],
            "max": commit["maximum_file_size"],
            "min": commit["minimum_file_size"],
        }


class added(object):
    name = "# of lines added"

    def __call__(self, commit, **kwargs):
        return commit["added"]


class test_added(object):
    name = "# of lines added in tests"

    def __call__(self, commit, **kwargs):
        return commit["test_added"]


class deleted(object):
    name = "# of lines deleted"

    def __call__(self, commit, **kwargs):
        return commit["deleted"]


class test_deleted(object):
    name = "# of lines deleted in tests"

    def __call__(self, commit, **kwargs):
        return commit["test_deleted"]


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
        f"sum {EXPERIENCE_TIMESPAN_TEXT_USER}": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_sum"
        ],
        f"max {EXPERIENCE_TIMESPAN_TEXT_USER}": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_max"
        ],
        f"min {EXPERIENCE_TIMESPAN_TEXT_USER}": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_min"
        ],
        f"avg {EXPERIENCE_TIMESPAN_TEXT_USER}": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_sum"
        ]
        / items_num
        if items_num > 0
        else 0,
        f"sum {EXPERIENCE_TIMESPAN_TEXT_USER} backout": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_backout_sum"
        ],
        f"max {EXPERIENCE_TIMESPAN_TEXT_USER} backout": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_backout_max"
        ],
        f"min {EXPERIENCE_TIMESPAN_TEXT_USER} backout": commit[
            f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_{exp_type}_backout_min"
        ],
        f"avg {EXPERIENCE_TIMESPAN_TEXT_USER} backout": commit[
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
            "total": commit["touched_prev_total_author_sum"],
            EXPERIENCE_TIMESPAN_TEXT_USER: commit[
                f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_author_sum"
            ],
            "total backouts": commit["touched_prev_total_author_backout_sum"],
            f"{EXPERIENCE_TIMESPAN_TEXT_USER} backouts": commit[
                f"touched_prev_{EXPERIENCE_TIMESPAN_TEXT}_author_backout_sum"
            ],
            "seniority": commit["seniority_author"] / 86400,
        }


class reviewer_experience(object):
    name = "Reviewer experience"

    def __call__(self, commit, **kwargs):
        return get_exps("reviewer", commit)


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
    name = "# of times the components were touched before"

    def __call__(self, commit, **kwargs):
        return get_exps("component", commit)


class directories(object):
    def __call__(self, commit, **kwargs):
        return commit["directories"]


class directories_modified_num(object):
    name = "# of directories modified"

    def __call__(self, commit, **kwargs):
        return len(commit["directories"])


class directory_touched_prev(object):
    name = "# of times the directories were touched before"

    def __call__(self, commit, **kwargs):
        return get_exps("directory", commit)


class files(object):
    def __init__(self, min_freq=0.00003):
        self.min_freq = min_freq

    def fit(self, commits):
        self.count = defaultdict(int)

        for commit in commits:
            for f in commit["files"]:
                self.count[f] += 1
        self.total_files = sum(self.count.values())

    def __call__(self, commit, **kwargs):
        return [
            f
            for f in commit["files"]
            if (self.count[f] / self.total_files) > self.min_freq
        ]


class file_touched_prev(object):
    name = "# of times the files were touched before"

    def __call__(self, commit, **kwargs):
        return get_exps("file", commit)


class types(object):
    name = "file types"

    def __call__(self, commit, **kwargs):
        return commit["types"]


class CommitExtractor(BaseEstimator, TransformerMixin):
    def __init__(self, feature_extractors, cleanup_functions):
        self.feature_extractors = feature_extractors
        self.cleanup_functions = cleanup_functions

    def fit(self, x, y=None):
        for feature in self.feature_extractors:
            if hasattr(feature, "fit"):
                feature.fit(x)

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

                if hasattr(feature_extractor, "name"):
                    feature_extractor_name = feature_extractor.name
                else:
                    feature_extractor_name = feature_extractor.__class__.__name__

                if isinstance(res, dict):
                    for key, value in res.items():
                        data[f"{feature_extractor_name} ({key})"] = value
                    continue

                if isinstance(res, list):
                    for item in res:
                        data[f"{item} in {feature_extractor_name}"] = "True"
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
