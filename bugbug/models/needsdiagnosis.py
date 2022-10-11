# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline

from bugbug import feature_cleanup, issue_features, utils
from bugbug.model import IssueModel

logger = logging.getLogger(__name__)


class NeedsDiagnosisModel(IssueModel):
    def __init__(self, lemmatization=False):
        IssueModel.__init__(
            self, owner="webcompat", repo="web-bugs", lemmatization=lemmatization
        )

        self.calculate_importance = False

        feature_extractors = []

        cleanup_functions = [
            feature_cleanup.fileref(),
            feature_cleanup.url(),
            feature_cleanup.synonyms(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "issue_extractor",
                    issue_features.IssueExtractor(
                        feature_extractors, cleanup_functions, rollback=True
                    ),
                ),
                (
                    "union",
                    ColumnTransformer(
                        [
                            ("title", self.text_vectorizer(min_df=0.0001), "title"),
                            (
                                "first_comment",
                                self.text_vectorizer(min_df=0.0001),
                                "first_comment",
                            ),
                        ]
                    ),
                ),
            ]
        )

        self.clf = xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count())
        self.clf.set_params(predictor="cpu_predictor")

    def get_labels(self):
        classes = {}

        for issue in self.github.get_issues():
            # Skip issues with empty title or body
            if issue["title"] is None or issue["body"] is None:
                continue

            # Skip issues that are not moderated yet as they don't have a meaningful title or body
            if issue["title"] == "In the moderation queue.":
                continue

            for event in issue["events"]:
                if event["event"] == "milestoned" and (
                    event["milestone"]["title"] == "needsdiagnosis"
                    or event["milestone"]["title"] == "moved"
                ):
                    classes[issue["number"]] = 0

            if issue["number"] not in classes:
                classes[issue["number"]] = 1

        logger.info(
            f"{sum(1 for label in classes.values() if label == 1)} issues have not been moved to needsdiagnosis"
        )
        logger.info(
            f"{sum(1 for label in classes.values() if label == 0)} issues have been moved to needsdiagnosis"
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()
