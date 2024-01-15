# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class WorksForMeModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.HasRegressionRange(),
            bug_features.Severity(),
            bug_features.Priority(),
            bug_features.HasCrashSignature(),
            bug_features.HasURL(),
            bug_features.Whiteboard(),
            bug_features.Product(),
            bug_features.Component(),
            bug_features.Keywords(),
            bug_features.TimeToClose(),
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
            ]
        )

        self.clf = Pipeline(
            [
                (
                    "union",
                    ColumnTransformer(
                        [
                            ("data", DictVectorizer(), "data"),
                            ("title", self.text_vectorizer(min_df=0.0001), "title"),
                            (
                                "comments",
                                self.text_vectorizer(min_df=0.0001),
                                "comments",
                            ),
                        ]
                    ),
                ),
                (
                    "estimator",
                    xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count()),
                ),
            ]
        )

    @staticmethod
    def _has_open_needinfo(bug):
        """Check if the bug has an open needinfo on the reporter."""
        for flag in bug["flags"]:
            if flag["name"] == "needinfo":
                return flag["requestee"] == bug["creator"] and flag["status"] == "?"

        return None

    def get_labels(self):
        classes = {}

        for bug in bugzilla.get_bugs():
            bug_id = int(bug["id"])

            if bug["resolution"] == "WORKSFORME":
                classes[bug_id] = 1
            else:
                if self._has_open_needinfo(bug):
                    classes[bug_id] = 1
                else:
                    classes[bug_id] = 0

        logger.info(
            "%d bugs are classified as not worksforme",
            sum(label == 0 for label in classes.values()),
        )
        logger.info(
            "%d bugs are classified as worksforme",
            sum(label == 1 for label in classes.values()),
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()
