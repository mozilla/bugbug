# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import xgboost
from imblearn.over_sampling import BorderlineSMOTE
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel


class SpamBugModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

        self.sampler = BorderlineSMOTE(random_state=0)
        self.calculate_importance = False

        feature_extractors = [
            bug_features.has_str(),
            bug_features.has_regression_range(),
            bug_features.severity(),
            bug_features.is_coverity_issue(),
            bug_features.has_crash_signature(),
            bug_features.has_url(),
            bug_features.has_w3c_url(),
            bug_features.has_github_url(),
            bug_features.whiteboard(),
            bug_features.product(),
            # TODO: We would like to use the component at the time of filing too,
            # but we can't because the rollback script doesn't support changes to
            # components yet.
            # bug_features.component(),
            bug_features.num_words_title(),
            bug_features.num_words_comments(),
            bug_features.keywords(),
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
            ]
        )

        self.clf = xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count())
        self.clf.set_params(predictor="cpu_predictor")

    def get_labels(self):
        classes = {}

        for bug_data in bugzilla.get_bugs(include_invalid=True):
            bug_id = bug_data["id"]

            # Skip bugs filed by Mozillians, since we are sure they are not spam.
            if "@mozilla" in bug_data["creator"]:
                continue

            # Legitimate bugs
            if bug_data["resolution"] == "FIXED":
                classes[bug_id] = 0

            # Spam bugs
            elif bug_data["product"] == "Invalid Bugs":
                classes[bug_id] = 1

        print(
            "{} bugs are classified as non-spam".format(
                sum(1 for label in classes.values() if label == 0)
            )
        )
        print(
            "{} bugs are classified as spam".format(
                sum(1 for label in classes.values() if label == 1)
            )
        )

        return classes, [0, 1]

    def items_gen(self, classes):
        # Overwriting this method to add include_invalid=True to get_bugs to
        # include spam bugs.
        return (
            (bug, classes[bug["id"]])
            for bug in bugzilla.get_bugs(include_invalid=True)
            if bug["id"] in classes
        )

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()

    def overwrite_classes(self, bugs, classes, probabilities):
        for (i, bug) in enumerate(bugs):
            if "@mozilla" in bug["creator"]:
                if probabilities:
                    classes[i] = [1.0, 0.0]
                else:
                    classes[i] = 0

        return classes
