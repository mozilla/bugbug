# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import re
import sys
from collections import defaultdict
from datetime import datetime
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
    name = "# of Comments made by Commenter in the past"

    def __call__(self, comment, commenter_experience, **kwargs):
        return commenter_experience


class CommentHasUnknownLink(CommentFeature):
    name = "Comment Has an Unknown Link"

    def __init__(self, domains_to_ignore=set()):
        self.domains_to_ignore = domains_to_ignore

        ignored_domains_pattern = "|".join(
            re.escape(domain) for domain in self.domains_to_ignore
        )
        self.url_pattern = re.compile(
            rf"http[s]?://(?!((?:{ignored_domains_pattern})\.\S+))\S+"
        )

    def __call__(self, comment, **kwargs) -> Any:
        return bool(self.url_pattern.search(comment["text"]))


class CharacterCount(CommentFeature):
    name = "# of Characters in the Comment"

    def __call__(self, comment, **kwargs):
        return len(comment["text"])


class WordCount(CommentFeature):
    name = "# of Words in the Comment"

    def __call__(self, comment, **kwargs):
        return len(comment["text"].split())


class DateCommentWasPosted(CommentFeature):
    name = "Date Comment Was Posted"

    def __call__(self, comment, **kwargs):
        comment_time = datetime.strptime(comment["creation_time"], "%Y-%m-%dT%H:%M:%SZ")
        return comment_time.strftime("%Y-%m-%d")


class TimeCommentWasPosted(CommentFeature):
    name = "Time Comment Was Posted"

    def __call__(self, comment, **kwargs):
        comment_time = datetime.strptime(comment["creation_time"], "%Y-%m-%dT%H:%M:%SZ")
        return comment_time.strftime("%H:%M:%S")


class CommentTags(CommentFeature):
    name = "Comment Tags"

    def __init__(self, to_ignore=set()):
        self.to_ignore = to_ignore

    def __call__(self, comment, **kwargs):
        tags = []
        for tag in comment["tags"]:
            if tag in self.to_ignore:
                continue

            tags.append(tag)
        return tags
