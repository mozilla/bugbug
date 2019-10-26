# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import xgboost
from imblearn.under_sampling import RandomUnderSampler
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup
from bugbug.model import BugModel


class SpamBugModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

        self.sampler = RandomUnderSampler(random_state=0)

        feature_extractors = [
            bug_features.has_str(),
            bug_features.has_regression_range(),
            bug_features.severity(),
            bug_features.is_coverity_issue(),
            bug_features.has_crash_signature(),
            bug_features.has_url(),
            bug_features.has_w3c_url(),
            bug_features.has_github_url(),
            bug_features.whiteboard(),
            bug_features.patches(),
            bug_features.landings(),
            bug_features.product(),
            bug_features.component(),
            bug_features.commit_added(),
            bug_features.commit_deleted(),
            bug_features.commit_types(),
        ]

        cleanup_functions = [
            feature_cleanup.fileref(),
            feature_cleanup.url(),
            feature_cleanup.synonyms(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "bug_extractor",
                    bug_features.BugExtractor(
                        feature_extractors, cleanup_functions, rollback=True
                    ),
                ),
                (
                    "union",
                    ColumnTransformer(
                        [
                            ("data", DictVectorizer(), "data"),
                            ("title", self.text_vectorizer(), "title"),
                            ("comments", self.text_vectorizer(), "comments"),
                        ]
                    ),
                ),
            ]
        )

        self.clf = xgboost.XGBClassifier(n_jobs=16)
        self.clf.set_params(predictor="cpu_predictor")

    def get_labels(self):
        classes = {}

        for bug_data in bugzilla.get_bugs():
            bug_id = bug_data["id"]

            # Legitimate bugs
            if bug_data["resolution"] == "FIXED":
                classes[bug_id] = 0

            # Spam bugs
            elif (
                bug_data["product"] == "Invalid Bugs"
                and bug_data["component"] == "General"
            ):
                classes[bug_id] = 1

        return classes, [0, 1]

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()
