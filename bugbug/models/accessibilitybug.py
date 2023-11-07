# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

import xgboost
from imblearn.over_sampling import BorderlineSMOTE
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SEVERITY = [
    "s1",
    "s2",
    "s3",
    "s4",
]


class AccessibilityBugModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

        self.sampler = BorderlineSMOTE(random_state=0)
        self.calculate_importance = False

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.Severity(),
            bug_features.Keywords(),
            bug_features.IsCoverityIssue(),
            bug_features.HasCrashSignature(),
            bug_features.HasURL(),
            bug_features.HasW3CURL(),
            bug_features.HasGithubURL(),
            bug_features.Whiteboard(),
            bug_features.Patches(),
            bug_features.Landings(),
            bug_features.BlockedBugsNumber(),
            bug_features.EverAffected(),
            bug_features.AffectedThenUnaffected(),
            bug_features.Product(),
            bug_features.Component(),
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

        self.clf = xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count())

    def get_labels(self):
        classes = {}

        for bug_data in bugzilla.get_bugs():
            bug_id = int(bug_data["id"])

            # A bug that was  manually labelled "access" is an access bug.
            if "access" in bug_data["keywords"]:
                classes[bug_id] = 1

            # A bug with the accessibility severity flag set is also an accessibility bug
            elif bug_data.get("cf_accessibility_severity") in SEVERITY:
                classes[bug_id] = 1

            else:
                classes[bug_id] = 0

        logger.info(
            "%d bugs are classified as non-accessibility",
            sum(1 for label in classes.values() if label == 0),
        )
        logger.info(
            "%d bugs are classified as accessibility",
            sum(1 for label in classes.values() if label == 1),
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names_out()

    def overwrite_classes(self, bugs, classes, probabilities):
        for i, bug in enumerate(bugs):
            if ("access" in bug["keywords"]) or (
                bug["cf_accessibility_severity"] in SEVERITY
            ):
                classes[i] = [1.0, 0.0] if probabilities else 1
        return classes
