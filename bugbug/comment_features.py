# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import sys
from collections import defaultdict
from typing import Any

import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin


class CommentFeature(object):
    pass


class CommentExtractor(BaseEstimator, TransformerMixin):
    def __init__(
        self,
        feature_extractors,
        cleanup_functions,
    ):
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

    def transform(self, comments):
        comments_iter = iter(comments())

        commenter_experience_map = defaultdict(int)

        def apply_transform(comment):
            data = {}

            for feature_extractor in self.feature_extractors:
                res = feature_extractor(
                    comment,
                    commenter_experience=commenter_experience_map[comment["creator"]],
                )

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

            commenter_experience_map[comment["creator"]] += 1

            comment_text = comment["text"]
            for cleanup_function in self.cleanup_functions:
                comment_text = cleanup_function(comment_text)

            return {
                "data": data,
                "comment_text": comment_text,
            }

        return pd.DataFrame(apply_transform(comment) for comment in comments_iter)


class CommenterExperience(CommentFeature):
    name = "#of Comments made by Commenter before"

    def __call__(self, comment, commenter_experience, **kwargs):
        return commenter_experience


class CommentTextHasKeywords(CommentFeature):
    name = "Comment Has Certain Keywords"

    def __init__(self, keywords=set()):
        self.keywords = keywords

    def __call__(self, comment, **kwargs):
        return any(keyword in comment["text"].lower() for keyword in self.keywords)


class CommentHasLink(CommentFeature):
    name = "Comment Has a Link"

    def __call__(self, comment, **kwargs) -> Any:
        return "http" in comment["text"]


class LengthofComment(CommentFeature):
    name = "Length of Comment"

    def __call__(self, comment, **kwargs):
        return len(comment["text"])


class TimeCommentWasPosted(CommentFeature):
    name = "Time Comment Was Posted"

    def __call__(self, comment, **kwargs):
        pass


class TimeDifferenceCommentAccountCreation(CommentFeature):
    name = "Time Difference Between Account Creation and when Comment was Made "

    def __call__(self, comment, prev_comment_time, **kwargs):
        pass


class CommentTags(CommentFeature):
    name = "Comment Tags"

    def __call__(self, comment, **kwargs):
        pass
