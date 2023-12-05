# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import xgboost
from imblearn.pipeline import Pipeline as ImblearnPipeline
from imblearn.under_sampling import InstanceHardnessThreshold
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, labels, utils
from bugbug.model import BugModel


class TrackingModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

        self.calculate_importance = False

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.HasRegressionRange(),
            bug_features.Severity(),
            bug_features.Keywords(),
            bug_features.IsCoverityIssue(),
            bug_features.HasCrashSignature(),
            bug_features.HasURL(),
            bug_features.HasW3CURL(),
            bug_features.HasGithubURL(),
            bug_features.Whiteboard(),
            bug_features.Patches(),
            bug_features.Landings(),
            bug_features.Product(),
            bug_features.Component(),
            bug_features.IsMozillian(),
            bug_features.BugReporter(),
            bug_features.BlockedBugsNumber(),
            bug_features.Priority(),
            bug_features.HasCVEInAlias(),
            bug_features.CommentCount(),
            bug_features.CommentLength(),
            bug_features.ReporterExperience(),
            bug_features.NumberOfBugDependencies(),
        ]

        cleanup_functions = [
            feature_cleanup.url(),
            feature_cleanup.fileref(),
            feature_cleanup.hex(),
            feature_cleanup.dll(),
            feature_cleanup.synonyms(),
            feature_cleanup.crash(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "bug_extractor",
                    bug_features.BugExtractor(
                        feature_extractors,
                        cleanup_functions,
                        rollback=True,
                        rollback_when=self.rollback,
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
                ("sampler", InstanceHardnessThreshold(random_state=0)),
                (
                    "estimator",
                    xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count()),
                ),
            ]
        )

    def rollback(self, change):
        return change["field_name"].startswith("cf_tracking_firefox")

    def get_labels(self):
        classes = {}

        for bug_id, category in labels.get_labels("tracking"):
            assert category in ["True", "False"], f"unexpected category {category}"
            classes[int(bug_id)] = 1 if category == "True" else 0

        for bug_data in bugzilla.get_bugs():
            bug_id = int(bug_data["id"])

            flag_found = False
            tracking_flags = [
                flag
                for flag in bug_data.keys()
                if flag.startswith("cf_tracking_firefox")
            ]
            for tracking_flag in tracking_flags:
                if bug_data[tracking_flag] in ["blocking", "+"]:
                    classes[bug_id] = 1
                    flag_found = True
                elif bug_data[tracking_flag] == "-":
                    classes[bug_id] = 0
                    flag_found = True

            if not flag_found:
                for entry in bug_data["history"]:
                    for change in entry["changes"]:
                        if change["field_name"].startswith("cf_tracking_firefox"):
                            if change["added"] in ["blocking", "+"]:
                                classes[bug_id] = 1
                            elif change["added"] == "-":
                                classes[bug_id] = 0

            if bug_data["resolution"] in ["INVALID", "DUPLICATE"]:
                continue

            if bug_id not in classes:
                classes[bug_id] = 0

        return classes, [0, 1]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()

    def overwrite_classes(self, bugs, classes, probabilities):
        for i, bug in enumerate(bugs):
            if bug["resolution"] in ["INVALID", "DUPLICATE"]:
                classes[i] = 0 if not probabilities else [1.0, 0.0]

        return classes
