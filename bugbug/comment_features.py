# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import sys
from collections import defaultdict
from datetime import datetime
from typing import Any

import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

from bugbug.utils import extract_urls_and_domains


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

                if isinstance(res, dict):
                    for key, value in res.items():
                        data[sys.intern(key)] = value
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


class NumberOfLinks(CommentFeature):
    name = "Number of Links in the comment"

    def __init__(self, domains_to_ignore=set()):
        self.known_domains = domains_to_ignore

    def __call__(self, comment, **kwargs) -> Any:
        domains = extract_urls_and_domains(comment["text"])["domains"]

        return {
            "# of Known links": sum(domain in self.known_domains for domain in domains),
            "# of Unknown links": sum(
                domain not in self.known_domains for domain in domains
            ),
            "Total # of links": len(domains),
        }


class CharacterCount(CommentFeature):
    name = "# of Characters in the Comment"

    def __call__(self, comment, **kwargs):
        return len(comment["text"])


class WordCount(CommentFeature):
    name = "# of Words in the Comment"

    def __call__(self, comment, **kwargs):
        return len(comment["text"].split())


class UnknownLinkAtBeginning(CommentFeature):
    name = "Unknown Link found at Beginning of the Comment"

    def __init__(self, domains_to_ignore=set()):
        self.known_domains = domains_to_ignore

    def __call__(self, comment, **kwargs):
        urls = extract_urls_and_domains(comment["text"], self.known_domains)["urls"]

        words = comment["text"].split()
        return words[0] in urls if words else False


class UnknownLinkAtEnd(CommentFeature):
    name = "Unknown Link found at End of the Comment"

    def __init__(self, domains_to_ignore=set()):
        self.known_domains = domains_to_ignore

    def __call__(self, comment, **kwargs):
        urls = extract_urls_and_domains(comment["text"], self.known_domains)["urls"]

        words = comment["text"].split()
        return words[-1] in urls if words else False


class HourOfDay(CommentFeature):
    name = "Hour of the Day (0-23)"

    def __call__(self, comment, **kwargs):
        comment_time = datetime.strptime(comment["creation_time"], "%Y-%m-%dT%H:%M:%SZ")
        return comment_time.hour


class Weekday(CommentFeature):
    name = "Day of the Week (0-7)"

    def __call__(self, comment, **kwargs):
        comment_time = datetime.strptime(comment["creation_time"], "%Y-%m-%dT%H:%M:%SZ")
        return comment_time.isoweekday()


class PostedOnWeekend(CommentFeature):
    name = "Comment was Posted on Weekend"

    def __call__(self, comment, **kwargs):
        comment_time = datetime.strptime(comment["creation_time"], "%Y-%m-%dT%H:%M:%SZ")
        return comment_time.isoweekday() in (5, 6)


class DayOfYear(CommentFeature):
    name = "Day of the Year (0-366)"

    def __call__(self, comment, **kwargs):
        comment_time = datetime.strptime(comment["creation_time"], "%Y-%m-%dT%H:%M:%SZ")
        return comment_time.timetuple().tm_yday


class WeekOfYear(CommentFeature):
    name = "Week of Year"

    def __call__(self, comment, **kwargs):
        comment_time = datetime.strptime(comment["creation_time"], "%Y-%m-%dT%H:%M:%SZ")
        return comment_time.isocalendar()[1]


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