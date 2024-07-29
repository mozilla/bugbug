# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import re

import numpy as np
import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelBinarizer

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel

# For the moment, rca - XYZ is treated of bugtype XYZ,
# so we don't need to store it in a dictionary.
RCA_CATEGORIES = [
    "requirementerror",
    "poorarchitecture",
    "designerror",
    "codingerror",
    "testingerror",
    "externalsoftwareaffectingfirefox",
    "performanceerror",
    "standards",
    "systemerror",
    "localizationerror",
    "memory",
    "infrastructure/builderror",
    "communicationissues",
    "productdecision",
    "undocumentedchange",
    "cornercase",
]

RCA_SUBCATEGORIES = [
    "codingerror-syntaxerror",
    "codingerror-logicalerror",
    "codingerror-semanticerror",
    "codingerror-runtimeerror",
    "codingerror-unhandledexceptions",
    "codingerror-internalapiissue",
    "codingerror-networkissue",
    "codingerror-compatibilityissue",
    "codingerror-other",
]

logger = logging.getLogger(__name__)


class RCATypeModel(BugModel):
    def __init__(
        self, lemmatization=False, historical=False, rca_subcategories_enabled=False
    ):
        BugModel.__init__(self, lemmatization)

        self.calculate_importance = False
        self.rca_subcategories_enabled = rca_subcategories_enabled

        self.le = LabelBinarizer()

        # should we consider only the main category or all sub categories
        self.RCA_TYPES = (
            RCA_SUBCATEGORIES + RCA_CATEGORIES
            if rca_subcategories_enabled
            else RCA_CATEGORIES
        )

        self.RCA_LIST = sorted(set(self.RCA_TYPES))

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.Severity(),
            bug_features.IsCoverityIssue(),
            bug_features.HasCrashSignature(),
            bug_features.HasURL(),
            bug_features.HasW3CURL(),
            bug_features.HasGithubURL(),
            # Ignore whiteboards that would make the ML completely skewed
            # bug_features.whiteboard(),
            bug_features.Patches(),
            bug_features.Landings(),
            bug_features.BlockedBugsNumber(),
            bug_features.EverAffected(),
            bug_features.AffectedThenUnaffected(),
            bug_features.Product(),
            bug_features.Component(),
        ]

        cleanup_functions = [
            feature_cleanup.url(),
            feature_cleanup.fileref(),
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

        self.clf = Pipeline(
            [
                (
                    "union",
                    ColumnTransformer(
                        [
                            ("data", DictVectorizer(), "data"),
                            ("title", self.text_vectorizer(min_df=0.001), "title"),
                            (
                                "first_comment",
                                self.text_vectorizer(min_df=0.001),
                                "first_comment",
                            ),
                            (
                                "comments",
                                self.text_vectorizer(min_df=0.001),
                                "comments",
                            ),
                        ]
                    ),
                ),
                (
                    "estimator",
                    OneVsRestClassifier(
                        xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count())
                    ),
                ),
            ]
        )

    # return rca from a whiteboard string
    def get_rca_from_whiteboard(self, whiteboard_data):
        rca = []
        whiteboard_data = re.sub(" ", "", whiteboard_data).lower()
        for whiteboard in whiteboard_data.split("["):
            if not any(whiteboard.startswith(s) for s in ("rca-", "rca:")):
                continue

            rca_whiteboard = re.sub("]", "", whiteboard)

            # Hybrid cases: rca:X-Y
            rca_whiteboard = re.sub(":", "-", rca_whiteboard)

            rca_whiteboard_split = (
                rca_whiteboard.split("-", 1)
                if self.rca_subcategories_enabled
                else rca_whiteboard.split("-")
            )

            if rca_whiteboard_split[1] not in self.RCA_LIST:
                logger.warning(rca_whiteboard_split[1] + " not in RCA_LIST")
            else:
                rca.append(rca_whiteboard_split[1])
        return rca

    def get_labels(self):
        classes = {}
        for bug in bugzilla.get_bugs():
            target = np.zeros(len(self.RCA_LIST))
            for rca in self.get_rca_from_whiteboard(bug["whiteboard"]):
                target[self.RCA_LIST.index(rca)] = 1
            classes[bug["id"]] = target
        return classes, self.RCA_LIST

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()

    def overwrite_classes(self, bugs, classes, probabilities):
        rca_values = self.get_rca(bugs)
        for i in len(classes):
            for rca in rca_values[i]:
                if rca in self.RCA_LIST:
                    if probabilities:
                        classes[i][self.RCA_LIST.index(rca)] = 1.0
                    else:
                        classes[i][self.RCA_LIST.index(rca)] = 1

        return classes
