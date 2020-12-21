# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from typing import Dict, Iterable, List, Tuple

import numpy as np
import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel

KEYWORD_DICT = {
    "sec-critical": "security",
    "sec-high": "security",
    "sec-moderate": "security",
    "sec-low": "security",
    "sec-other": "security",
    "sec-audit": "security",
    "sec-vector": "security",
    "sec-want": "security",
    "memory-footprint": "memory",
    "memory-leak": "memory",
    "crash": "crash",
    "crashreportid": "crash",
    "perf": "performance",
}
TYPE_LIST = sorted(set(KEYWORD_DICT.values()))


def bug_to_types(bug: bugzilla.BugDict) -> List[str]:
    return list(
        set(
            KEYWORD_DICT[keyword]
            for keyword in bug["keywords"]
            if keyword in KEYWORD_DICT
        )
    )


class BugTypeModel(BugModel):
    def __init__(self, lemmatization=False, historical=False):
        BugModel.__init__(self, lemmatization)

        self.calculate_importance = False

        feature_extractors = [
            bug_features.has_str(),
            bug_features.severity(),
            # Ignore keywords that would make the ML completely skewed
            # (we are going to use them as 100% rules in the evaluation phase).
            bug_features.keywords(set(KEYWORD_DICT.keys())),
            bug_features.is_coverity_issue(),
            bug_features.has_crash_signature(),
            bug_features.has_url(),
            bug_features.has_w3c_url(),
            bug_features.has_github_url(),
            bug_features.whiteboard(),
            bug_features.patches(),
            bug_features.landings(),
            bug_features.blocked_bugs_number(),
            bug_features.ever_affected(),
            bug_features.affected_then_unaffected(),
            bug_features.product(),
            bug_features.component(),
        ]

        cleanup_functions = [
            feature_cleanup.url(),
            feature_cleanup.fileref(),
            feature_cleanup.synonyms(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "bug_extractor",
                    bug_features.BugExtractor(feature_extractors, cleanup_functions),
                ),
                (
                    "union",
                    ColumnTransformer(
                        [
                            ("data", DictVectorizer(), "data"),
                            ("title", self.text_vectorizer(min_df=0.001), "title"),
                            (
                                "first_comment",
                                self.text_vectorizer(min_df=0.001),
                                "first_comment",
                            ),
                            (
                                "comments",
                                self.text_vectorizer(min_df=0.001),
                                "comments",
                            ),
                        ]
                    ),
                ),
            ]
        )

        self.clf = OneVsRestClassifier(
            xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count())
        )

    def get_labels(self) -> Tuple[Dict[int, np.ndarray], List[str]]:
        classes = {}

        for bug_data in bugzilla.get_bugs():
            target = np.zeros(len(TYPE_LIST))
            for type_ in bug_to_types(bug_data):
                target[TYPE_LIST.index(type_)] = 1

            classes[int(bug_data["id"])] = target

        return classes, TYPE_LIST

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()

    def overwrite_classes(
        self,
        bugs: Iterable[bugzilla.BugDict],
        classes: Dict[int, np.ndarray],
        probabilities: bool,
    ):
        for i, bug in enumerate(bugs):
            for type_ in bug_to_types(bug):
                if probabilities:
                    classes[i][TYPE_LIST.index(type_)] = 1.0
                else:
                    classes[i][TYPE_LIST.index(type_)] = 1

        return classes
