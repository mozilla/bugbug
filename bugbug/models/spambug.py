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


class SpamBugModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

        self.calculate_importance = False

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.HasRegressionRange(),
            bug_features.Severity(),
            bug_features.HasCrashSignature(),
            bug_features.HasURL(),
            bug_features.Whiteboard(),
            bug_features.Product(),
            # TODO: We would like to use the component at the time of filing too,
            # but we can't because the rollback script doesn't support changes to
            # components yet.
            # bug_features.component(),
            bug_features.NumWordsTitle(),
            bug_features.NumWordsComments(),
            bug_features.Keywords(),
            bug_features.Priority(),
            bug_features.Version(),
            bug_features.TargetMilestone(),
            bug_features.HasAttachment(),
            bug_features.Platform(),
            bug_features.OpSys(),
            bug_features.FiledVia(),
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
                    xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count()),
                ),
            ]
        )

    def get_labels(self):
        classes = {}

        for bug_data in bugzilla.get_bugs(
            include_invalid=True,
            include_additional_products=bugzilla.ADDITIONAL_PRODUCTS,
        ):
            bug_id = bug_data["id"]

            # Skip bugs filed by Mozillians, since we are sure they are not spam.
            if "@mozilla" in bug_data["creator"]:
                continue

            # A bug that was moved out of 'Invalid Bugs' is definitely a legitimate bug.
            for history in bug_data["history"]:
                for change in history["changes"]:
                    if (
                        change["field_name"] == "product"
                        and change["removed"] == "Invalid Bugs"
                    ):
                        classes[bug_id] = 0

            # A fixed bug is definitely a legitimate bug.
            if bug_data["resolution"] == "FIXED":
                classes[bug_id] = 0

            # A bug in the 'Invalid Bugs' product is definitely a spam bug.
            elif bug_data["product"] == "Invalid Bugs":
                classes[bug_id] = 1

        logger.info(
            "%d bugs are classified as non-spam",
            sum(label == 0 for label in classes.values()),
        )
        logger.info(
            "%d bugs are classified as spam",
            sum(label == 1 for label in classes.values()),
        )

        return classes, [0, 1]

    def items_gen(self, classes):
        # Overwriting this method to add include_invalid=True and include_additional_products=ADDITIONAL_PRODUCTS
        # for get_bugs to include spam bugs and all additional products
        return (
            (bug, classes[bug["id"]])
            for bug in bugzilla.get_bugs(
                include_invalid=True,
                include_additional_products=bugzilla.ADDITIONAL_PRODUCTS,
            )
            if bug["id"] in classes
        )

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()

    def overwrite_classes(self, bugs, classes, probabilities):
        for i, bug in enumerate(bugs):
            if "@mozilla" in bug["creator"]:
                if probabilities:
                    classes[i] = [1.0, 0.0]
                else:
                    classes[i] = 0

        return classes
