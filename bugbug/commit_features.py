# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin


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


class author_experience(object):
    def __call__(self, commit, **kwargs):
        return commit["author_experience"]


class author_experience_90_days(object):
    def __call__(self, commit, **kwargs):
        return commit["author_experience_90_days"]


class reviewer_experience(object):
    def __call__(self, commit, **kwargs):
        return commit["reviewer_experience"]


class reviewer_experience_90_days(object):
    def __call__(self, commit, **kwargs):
        return commit["reviewer_experience_90_days"]


class components_touched_prev(object):
    def __call__(self, commit, **kwargs):
        return commit["components_touched_prev"]


class components_touched_prev_90_days(object):
    def __call__(self, commit, **kwargs):
        return commit["components_touched_prev_90_days"]


class files_touched_prev(object):
    def __call__(self, commit, **kwargs):
        return commit["files_touched_prev"]


class files_touched_prev_90_days(object):
    def __call__(self, commit, **kwargs):
        return commit["files_touched_prev_90_days"]


class types(object):
    def __call__(self, commit, **kwargs):
        return commit["types"]


class components(object):
    def __call__(self, commit, **kwargs):
        return commit["components"]


class number_of_reviewers(object):
    def __call__(self, commit, **kwargs):
        return len(commit["reviewers"])


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

            for f in self.feature_extractors:
                res = f(commit)

                if res is None:
                    continue

                if isinstance(res, list):
                    for item in res:
                        data[f.__class__.__name__ + "-" + item] = "True"
                    continue

                if isinstance(res, bool):
                    res = str(res)

                data[f.__class__.__name__] = res

            # TODO: Try simply using all possible fields instead of extracting features manually.

            for cleanup_function in self.cleanup_functions:
                commit["desc"] = cleanup_function(commit["desc"])

            result = {"data": data, "desc": commit["desc"]}

            results.append(result)

        return pd.DataFrame(results)
