# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import sys

import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

from bugbug import issue_snapshot


class comment_count(object):
    name = "# of comments"

    def __call__(self, issue, **kwargs):
        return issue["comments"]


class IssueExtractor(BaseEstimator, TransformerMixin):
    def __init__(
        self,
        feature_extractors,
        cleanup_functions,
        rollback=False,
        rollback_when=None,
    ):
        assert len(set(type(fe) for fe in feature_extractors)) == len(
            feature_extractors
        ), "Duplicate Feature Extractors"
        self.feature_extractors = feature_extractors

        assert len(set(type(cf) for cf in cleanup_functions)) == len(
            cleanup_functions
        ), "Duplicate Cleanup Functions"
        self.cleanup_functions = cleanup_functions
        self.rollback = rollback
        self.rollback_when = rollback_when

    def fit(self, x, y=None):
        for feature in self.feature_extractors:
            if hasattr(feature, "fit"):
                feature.fit(x())

        return self

    def transform(self, issues):
        results = []

        for issue in issues():

            if self.rollback:
                issue = issue_snapshot.rollback(issue, self.rollback_when)

            data = {}

            for feature_extractor in self.feature_extractors:
                res = feature_extractor(issue)

                if hasattr(feature_extractor, "name"):
                    feature_extractor_name = feature_extractor.name
                else:
                    feature_extractor_name = feature_extractor.__class__.__name__

                if res is None:
                    continue

                if isinstance(res, (list, set)):
                    for item in res:
                        data[sys.intern(f"{item} in {feature_extractor_name}")] = True
                    continue

                data[feature_extractor_name] = res

            title = issue["title"]
            body = issue["body"]
            for cleanup_function in self.cleanup_functions:
                title = cleanup_function(title)
                body = cleanup_function(body)

            results.append(
                {
                    "data": data,
                    "title": title,
                    "first_comment": body,
                }
            )

        return pd.DataFrame(results)
