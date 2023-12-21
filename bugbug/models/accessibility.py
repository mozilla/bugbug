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
            bug_features.Keywords({"access"}),
            bug_features.HasAttachment(),
            bug_features.Product(),
            bug_features.FiledVia(),
            bug_features.HasImageAttachment(),
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
                                "comments",
                                self.text_vectorizer(min_df=0.0001),
                                "comments",
                            ),
                        ]
                    ),
                ),
                ("sampler", BorderlineSMOTE(random_state=0)),
                (
                    "estimator",
                    xgboost.XGBClassifier(
                        n_jobs=utils.get_physical_cpu_count(), scale_pos_weight=10
                    ),
                ),
            ]
        )

    @staticmethod
    def __is_accessibility_bug(bug):
        """Check if a bug is an accessibility bug."""
        return bug["cf_accessibility_severity"] != "---" or "access" in bug["keywords"]

    @staticmethod
    def __get_access_bugs_ids():
        """Get accessibility related bugs not older than 4 years and 6 months ago."""
        four_years_and_six_months_ago = datetime.utcnow() - relativedelta(
            years=4, months=6
        )
        params = {
            "f1": "creation_ts",
            "o1": "greaterthan",
            "v1": four_years_and_six_months_ago.strftime("%Y-%m-%d"),
            "f2": "OP",
            "j2": "OR",
            "f3": "cf_accessibility_severity",
            "o3": "notequals",
            "v3": "---",
            "f4": "keywords",
            "o4": "substring",
            "v4": "access",
            "product": bugzilla.PRODUCTS,
        }

        return bugzilla.get_ids(params)

    def get_labels(self):
        classes = {}

        access_bugs = self.__get_access_bugs_ids()
        bugzilla.download_bugs(access_bugs)

        for bug in bugzilla.get_bugs():
            bug_id = int(bug["id"])

            if "cf_accessibility_severity" not in bug:
                continue

            classes[bug_id] = 1 if self.__is_accessibility_bug(bug) else 0

        positive_samples = sum(label == 1 for label in classes.values())
        negative_samples = sum(label == 0 for label in classes.values())

        logger.info(
            "%d bugs are classified as non-accessibility",
            negative_samples,
        )
        logger.info(
            "%d bugs are classified as accessibility",
            positive_samples,
        )

        ratio = round((negative_samples / positive_samples) ** 0.5)

        self.clf.named_steps["estimator"].set_params(scale_pos_weight=ratio)

        return classes, [0, 1]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()

    def overwrite_classes(self, bugs, classes, probabilities):
        for i, bug in enumerate(bugs):
            if self.__is_accessibility_bug(bug):
                classes[i] = [1.0, 0.0] if probabilities else 1
        return classes
