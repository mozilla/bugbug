# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

import xgboost
from imblearn.pipeline import Pipeline as ImblearnPipeline
from imblearn.under_sampling import RandomUnderSampler
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class StepsToReproduceModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

        feature_extractors = [
            bug_features.HasRegressionRange(),
            bug_features.Severity(),
            bug_features.Keywords({"stepswanted"}),
            bug_features.IsCoverityIssue(),
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
                    bug_features.BugExtractor(feature_extractors, cleanup_functions),
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
                            ("title", self.text_vectorizer(), "title"),
                            ("comments", self.text_vectorizer(), "comments"),
                        ]
                    ),
                ),
                ("sampler", RandomUnderSampler(random_state=0)),
                (
                    "estimator",
                    xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count()),
                ),
            ]
        )

    def get_labels(self):
        classes = {}

        for bug_data in bugzilla.get_bugs():
            if bug_data["type"] != "defect":
                continue
            if "cf_has_str" in bug_data:
                if bug_data["cf_has_str"] == "no":
                    classes[int(bug_data["id"])] = 0
                elif bug_data["cf_has_str"] == "yes":
                    classes[int(bug_data["id"])] = 1
            elif "stepswanted" in bug_data["keywords"]:
                classes[int(bug_data["id"])] = 0
            else:
                for entry in bug_data["history"]:
                    for change in entry["changes"]:
                        if change["removed"].startswith("stepswanted"):
                            classes[int(bug_data["id"])] = 1

        logger.info(
            "%d bugs have no steps to reproduce",
            sum(label == 0 for label in classes.values()),
        )
        logger.info(
            "%d bugs have steps to reproduce",
            sum(label == 1 for label in classes.values()),
        )

        return classes, [0, 1]

    def overwrite_classes(self, bugs, classes, probabilities):
        for i, bug in enumerate(bugs):
            if "cf_has_str" in bug and bug["cf_has_str"] == "no":
                classes[i] = 0 if not probabilities else [1.0, 0.0]
            elif "cf_has_str" in bug and bug["cf_has_str"] == "yes":
                classes[i] = 1 if not probabilities else [0.0, 1.0]
            elif "stepswanted" in bug["keywords"]:
                classes[i] = 0 if not probabilities else [1.0, 0.0]

        return classes

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()
