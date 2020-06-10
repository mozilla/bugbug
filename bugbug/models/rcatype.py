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

        # should we consider only the main category or all sub categories
        self.RCA_TYPES = (
            RCA_SUBCATEGORIES + RCA_CATEGORIES
            if rca_subcategories_enabled
            else RCA_CATEGORIES
        )

        self.RCA_LIST = sorted(set(self.RCA_TYPES))

        feature_extractors = [
            bug_features.has_str(),
            bug_features.severity(),
            bug_features.is_coverity_issue(),
            bug_features.has_crash_signature(),
            bug_features.has_url(),
            bug_features.has_w3c_url(),
            bug_features.has_github_url(),
            # Ignore whiteboards that would make the ML completely skewed
            # bug_features.whiteboard(),
            bug_features.patches(),
            bug_features.landings(),
            bug_features.blocked_bugs_number(),
            bug_features.ever_affected(),
            bug_features.affected_then_unaffected(),
            bug_features.product(),
            bug_features.component(),
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
            ]
        )

        self.clf = OneVsRestClassifier(
            xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count())
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
        return self.extraction_pipeline.named_steps["union"].get_feature_names()

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
