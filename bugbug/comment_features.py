# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import sys
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np
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

    def transform(self, items):
        items_iter = iter(items())

        def apply_transform(item):
            _, comment = item
            data = {}

            for feature_extractor in self.feature_extractors:
                res = feature_extractor(
                    item,
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
                    for value in res:
                        data[sys.intern(f"{value} in {feature_extractor_name}")] = True
                    continue

                data[feature_extractor_name] = res

            comment_text = comment["text"]
            for cleanup_function in self.cleanup_functions:
                comment_text = cleanup_function(comment_text)

            return {
                "data": data,
                "comment_text": comment_text,
            }

        return pd.DataFrame(apply_transform(item) for item in items_iter)


class CommentCreatorIsBugCreator(CommentFeature):
    name = "Comment Creator is the Bug Creator"

    def __call__(self, item, **kwargs) -> Any:
        bug, comment = item

        return bug["creator"] == comment["creator"]


class CommenterExperience(CommentFeature):
    name = "# of Comments Previously Posted by the Commenter"

    def __init__(self):
        # During training, the sorted timestamps of the comments of each
        # commenter. After loading a saved model, only the number of comments.
        self.comment_times: dict[str, np.ndarray | int] = {}

    @staticmethod
    def _timestamp(comment) -> int:
        return int(datetime.fromisoformat(comment["creation_time"]).timestamp())

    def fit(self, items):
        """Store when each commenter posted their comments.

        All the comments of the bugs in `items` are used, not only the
        comments in `items`, so that the bug descriptions and the comments
        that are not part of the dataset are also counted.
        """
        seen_bugs = set()
        comment_times = defaultdict(list)

        for bug, _ in items:
            if bug["id"] in seen_bugs:
                continue
            seen_bugs.add(bug["id"])

            for comment in bug["comments"]:
                comment_times[comment["creator"]].append(self._timestamp(comment))

        self.comment_times = {
            creator: np.sort(np.array(times, dtype=np.int64))
            for creator, times in comment_times.items()
        }

        return self

    def __getstate__(self):
        # The comments classified after training are newer than all the comments
        # seen during training, so we only need to keep the number of comments of
        # each commenter. This keeps the saved model small.
        state = self.__dict__.copy()
        state["comment_times"] = {
            creator: len(times) if isinstance(times, np.ndarray) else times
            for creator, times in self.comment_times.items()
        }
        return state

    def __call__(self, item, **kwargs):
        _, comment = item

        times = self.comment_times.get(comment["creator"])
        if times is None:
            return 0

        if isinstance(times, int):
            return times

        # Only count the comments posted before this one.
        return int(np.searchsorted(times, self._timestamp(comment), side="left"))


class NumberOfLinks(CommentFeature):
    name = "Number of Links in the comment"

    def __init__(self, domains_to_ignore=None):
        self.known_domains = domains_to_ignore if domains_to_ignore else set()

    def __call__(self, item, **kwargs) -> Any:
        _, comment = item

        domains = extract_urls_and_domains(comment["text"])["domains"]

        return {
            "# of Known links": sum(domain in self.known_domains for domain in domains),
            "# of Unknown links": sum(
                domain not in self.known_domains for domain in domains
            ),
            "Total # of links": len(domains),
        }


class WordCount(CommentFeature):
    name = "# of Words in the Comment"

    def __call__(self, item, **kwargs):
        _, comment = item

        return len(comment["text"].split())


class UnknownLinkPositions(CommentFeature):
    name = "Position of Unknown Links in the Comment"

    def __init__(self, domains_to_ignore=None):
        self.known_domains = domains_to_ignore if domains_to_ignore else set()

    def __call__(self, item, **kwargs):
        _, comment = item

        text = comment["text"].strip()
        spans = extract_urls_and_domains(text, self.known_domains)["spans"]

        if not spans:
            return {
                "Start of the first unknown link": -1,
                "End of the last unknown link": -1,
                "# of characters after the last unknown link": -1,
            }

        return {
            "Start of the first unknown link": spans[0][0],
            "End of the last unknown link": spans[-1][1],
            "# of characters after the last unknown link": len(text) - spans[-1][1],
        }


class HourOfDay(CommentFeature):
    name = "Hour of the Day (0-23)"

    def __call__(self, item, **kwargs):
        _, comment = item

        comment_time = datetime.strptime(comment["creation_time"], "%Y-%m-%dT%H:%M:%SZ")
        return comment_time.hour


class Weekday(CommentFeature):
    name = "Day of the Week (1-7)"

    def __call__(self, item, **kwargs):
        _, comment = item

        comment_time = datetime.strptime(comment["creation_time"], "%Y-%m-%dT%H:%M:%SZ")
        return comment_time.isoweekday()


class DayOfYear(CommentFeature):
    name = "Day of the Year (1-366)"

    def __call__(self, item, **kwargs):
        _, comment = item

        comment_time = datetime.strptime(comment["creation_time"], "%Y-%m-%dT%H:%M:%SZ")
        return comment_time.timetuple().tm_yday
