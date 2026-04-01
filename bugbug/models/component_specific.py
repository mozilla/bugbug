# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from datetime import datetime, timezone

import dateutil.parser
import xgboost
from dateutil.relativedelta import relativedelta
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ComponentSpecificModel(BugModel):
    def __init__(self, lemmatization=False, product="Firefox", component="General"):
        BugModel.__init__(self, lemmatization)

        self.product = product
        self.component = component

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.Severity(),
            bug_features.Keywords(),
            bug_features.HasCrashSignature(),
            bug_features.HasURL(),
            bug_features.HasW3CURL(),
            bug_features.HasGithubURL(),
            bug_features.Whiteboard(),
            bug_features.Patches(),
            bug_features.Landings(),
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

    def get_labels(self):
        classes = {}

        for bug_data in bugzilla.get_bugs():
            if dateutil.parser.parse(bug_data["creation_time"]) < datetime.now(
                timezone.utc
            ) - relativedelta(years=3):
                continue

            # Only bugs that were moved out of General and into a specific component
            # Or the opposite

            for history in bug_data["history"]:
                to_product_firefox = False
                to_component_general = False

                from_product_firefox = False
                from_component_general = False

                for change in history["changes"]:
                    if change["field_name"] == "product":
                        if change["added"] == self.product:
                            to_product_firefox = True
                        elif change["removed"] == self.product:
                            from_product_firefox = True

                    if change["field_name"] == "component":
                        if change["added"] == self.component:
                            to_component_general = True
                        elif change["removed"] == self.component:
                            from_component_general = True

                if from_product_firefox and from_component_general:
                    classes[bug_data["id"]] = 1
                elif to_product_firefox and to_component_general:
                    classes[bug_data["id"]] = 0

        logger.info(
            "%d bugs were moved out of %s::%s",
            sum(label == 1 for label in classes.values()),
            self.product,
            self.component,
        )
        logger.info(
            "%d bugs were moved in %s::%s",
            sum(label == 0 for label in classes.values()),
            self.product,
            self.component,
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()
