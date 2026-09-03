# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

import xgboost
from imblearn.over_sampling import BorderlineSMOTE
from imblearn.pipeline import Pipeline as ImblearnPipeline
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PerformanceBugModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

        self.calculate_importance = False

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.Keywords(
                prefixes_to_ignore=bug_features.IsPerformanceBug.keyword_prefixes
            ),
            bug_features.IsCoverityIssue(),
            bug_features.HasCrashSignature(),
            bug_features.HasURL(),
            bug_features.HasW3CURL(),
            bug_features.HasGithubURL(),
            bug_features.Product(),
            bug_features.HasRegressionRange(),
            bug_features.HasCVEInAlias(),
            bug_features.HasAttachment(),
            bug_features.FiledVia(),
            bug_features.BugType(),
        ]

        cleanup_functions = [
            feature_cleanup.fileref(),
            feature_cleanup.url(),
            feature_cleanup.synonyms(),
            feature_cleanup.hex(),
            feature_cleanup.dll(),
            feature_cleanup.crash(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "bug_extractor",
                    bug_features.BugExtractor(
                        feature_extractors, cleanup_functions, rollback=True
                    ),
                ),
            ]
        )

        self.clf = ImblearnPipeline(
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
                ("sampler", BorderlineSMOTE(random_state=0)),
                (
                    "estimator",
                    xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count()),
                ),
            ]
        )

    def get_labels(self):
        classes = {}
        is_performance_bug = bug_features.IsPerformanceBug()

        for bug_data in bugzilla.get_bugs():
            bug_id = int(bug_data["id"])

            if "cf_performance_impact" not in bug_data or bug_data[
                "cf_performance_impact"
            ] in ("?", "none"):
                continue

            classes[bug_id] = 1 if is_performance_bug(bug_data) else 0

        logger.info(
            "%d performance bugs",
            sum(label == 1 for label in classes.values()),
        )
        logger.info(
            "%d non-performance bugs",
            sum(label == 0 for label in classes.values()),
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()

    def overwrite_classes(self, bugs, classes, probabilities):
        is_performance_bug = bug_features.IsPerformanceBug()

        for i, bug in enumerate(bugs):
            if is_performance_bug(bug):
                classes[i] = [1.0, 0.0] if probabilities else 1

        return classes
