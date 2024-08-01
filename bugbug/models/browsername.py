# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import feature_cleanup, github, issue_features, utils
from bugbug.model import IssueModel

logger = logging.getLogger(__name__)


class BrowserNameModel(IssueModel):
    def __init__(self, lemmatization=False):
        IssueModel.__init__(self, lemmatization)

        feature_extractors = [
            issue_features.CommentCount(),
        ]

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
                        feature_extractors, cleanup_functions
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
                            ("data", DictVectorizer(), "data"),
                            ("title", self.text_vectorizer(min_df=0.0001), "title"),
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

    def get_labels(self):
        classes = {}

        for issue in github.get_issues():
            for label in issue["labels"]:
                if label["name"] == "browser-firefox":
                    classes[issue["number"]] = 1

            if issue["number"] not in classes:
                classes[issue["number"]] = 0

        logger.info(
            "%d issues belong to Firefox",
            sum(label == 1 for label in classes.values()),
        )
        logger.info(
            "%d issues do not belong to Firefox",
            sum(label == 0 for label in classes.values()),
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()
