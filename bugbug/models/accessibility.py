# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from datetime import datetime

import xgboost
from dateutil.relativedelta import relativedelta
from imblearn.over_sampling import BorderlineSMOTE
from imblearn.pipeline import Pipeline as ImblearnPipeline
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AccessibilityModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

        self.calculate_importance = False

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.Severity(),
            bug_features.Keywords(),
            bug_features.Whiteboard(),
            bug_features.HasImageAttachmentAtBugCreation(),
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
                    bug_features.BugExtractor(
                        feature_extractors,
                        cleanup_functions,
                        rollback=True,
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

    def is_accessbility_bug(self, bug_data):
        return (
            "access" in bug_data["keywords"]
            or bug_data.get("cf_accessibility_severity", "---") != "---"
        )

    def get_labels(self):
        classes = {}

        all_ids = self.get_access_ids(years=4, months=6) + self.get_access_sev_ids(
            years=4, months=6
        )

        bugzilla.download_bugs(all_ids)

        for bug_data in bugzilla.get_bugs():
            bug_id = int(bug_data["id"])

            classes[bug_id] = 1 if self.is_accessbility_bug(bug_data) else 0

        logger.info(
            "%d bugs are classified as non-accessibility",
            sum(label == 0 for label in classes.values()),
        )
        logger.info(
            "%d bugs are classified as accessibility",
            sum(label == 1 for label in classes.values()),
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()

    def overwrite_classes(self, bugs, classes, probabilities):
        for i, bug in enumerate(bugs):
            if self.is_accessbility_bug(bug):
                classes[i] = [1.0, 0.0] if probabilities else 1
        return classes

    def get_access_ids(self, years: int, months: int) -> list[int]:
        years_and_months_ago = datetime.utcnow() - relativedelta(
            years=years, months=months
        )
        access_params = {
            "f1": "creation_ts",
            "o1": "greaterthan",
            "v1": years_and_months_ago.strftime("%Y-%m-%d"),
            "keywords": "access",
        }

        return bugzilla.get_ids(access_params)

    def get_access_sev_ids(self, years: int, months: int) -> list[int]:
        years_and_months_ago = datetime.utcnow() - relativedelta(
            years=years, months=months
        )
        access_sev_params = {
            "f1": "creation_ts",
            "o1": "greaterthan",
            "v1": years_and_months_ago.strftime("%Y-%m-%d"),
            "cf_accessibility_severity": ["S1", "S2", "S3", "S4"],
        }

        return bugzilla.get_ids(access_sev_params)
