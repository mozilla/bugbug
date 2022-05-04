# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

import xgboost
from imblearn.over_sampling import BorderlineSMOTE
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import Pipeline
from tabulate import tabulate

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    "S1": 1,
    "critical": 1,
    "blocker": 1,
    "S2": 2,
    "major": 2,
    "S3": 3,
    "normal": 3,
    "S4": 4,
    "minor": 4,
    "trivial": 4,
    "enhancement": 4,
}

SEVERITY_LIST = sorted(set(SEVERITY_MAP.values()))


class BugSeverityModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

        self.sampler = BorderlineSMOTE(random_state=0)
        self.calculate_importance = False

        feature_extractors = [
            bug_features.has_str(),
            bug_features.has_regression_range(),
            bug_features.has_crash_signature(),
            bug_features.keywords(),
            bug_features.number_of_bug_dependencies(),
            bug_features.is_coverity_issue(),
            bug_features.has_url(),
            bug_features.has_w3c_url(),
            bug_features.has_github_url(),
            bug_features.whiteboard(),
            bug_features.product(),
            bug_features.is_mozillian(),
            bug_features.version(),
            bug_features.num_words_title(),
            bug_features.num_words_comments(),
            bug_features.has_image_attachment_at_bug_creation(),
            bug_features.platform(),
            bug_features.op_sys(),
        ]

        cleanup_functions = [
            feature_cleanup.url(),
            feature_cleanup.fileref(),
            feature_cleanup.dll(),
            feature_cleanup.synonyms(),
            feature_cleanup.crash(),
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

    def get_labels(self):
        classes = {}

        for bug_data in bugzilla.get_bugs():
            if bug_data["severity"] in ("N/A", "--"):
                continue

            classes[bug_data["id"]] = SEVERITY_MAP[bug_data["severity"]]

        print(
            tabulate(
                [
                    [
                        f"S{severity}",
                        sum(1 for target in classes.values() if target == severity),
                    ]
                    for severity in SEVERITY_LIST
                ],
                ["Severity", "# of Bugs"],
            )
        )

        return classes, SEVERITY_LIST

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()
