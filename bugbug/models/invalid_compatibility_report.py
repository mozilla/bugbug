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


class InvalidCompatibilityReportModel(IssueModel):
    def __init__(self, lemmatization=False):
        super().__init__(
            owner="webcompat", repo="web-bugs", lemmatization=lemmatization
        )

        self.calculate_importance = False

        feature_extractors = []

        cleanup_functions = []

        self.extraction_pipeline = Pipeline(
            [
                (
                    "report_extractor",
                    issue_features.IssueExtractor(
                        feature_extractors, cleanup_functions, rollback=False
                    ),
                ),
            ]
        )

        self.clf = Pipeline(
            [
                (
                    "union",
                    ColumnTransformer(
                        [
                            (
                                "first_comment",
                                self.text_vectorizer(min_df=0.0001),
                                "first_comment",
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

    def items_gen(self, classes):
        # Do cleanup separately from extraction pipeline to
        # make sure it's not applied during classification due to differences
        # in text structure between GitHub issues and reports
        cleanup_function = feature_cleanup.CleanCompatibilityReportDescription()

        for issue, label in super().items_gen(classes):
            issue = {
                **issue,
                "body": cleanup_function(issue["body"]),
            }
            yield issue, label

    def get_labels(self):
        classes = {}
        for issue in self.github.get_issues():
            if not issue["title"] or not issue["body"]:
                continue

            # Skip issues that are not moderated yet as they don't have a
            # meaningful title or body.
            if issue["title"] == "In the moderation queue.":
                continue

            if (
                issue["milestone"]
                and (issue["milestone"]["title"] in ("invalid", "incomplete"))
                and any(label["name"] == "wcrt-invalid" for label in issue["labels"])
            ):
                classes[issue["number"]] = 1

            elif any(
                event["event"] == "milestoned"
                and (event["milestone"]["title"] in ("needsdiagnosis", "moved"))
                for event in issue["events"]
            ):
                classes[issue["number"]] = 0

        logger.info(
            "%d issues have been moved to invalid",
            sum(label == 1 for label in classes.values()),
        )
        logger.info(
            "%d issues have not been moved to invalid",
            sum(label == 0 for label in classes.values()),
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()
