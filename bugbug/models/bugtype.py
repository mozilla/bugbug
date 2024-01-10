# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from typing import Iterable

import numpy as np
import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelBinarizer

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel

logger = logging.getLogger(__name__)


class BugTypeModel(BugModel):
    def __init__(self, lemmatization=False, historical=False):
        BugModel.__init__(self, lemmatization)

        self.calculate_importance = False

        self.le = LabelBinarizer()

        self.bug_type_extractors = bug_features.BugTypes.bug_type_extractors

        label_keyword_prefixes = {
            keyword
            for extractor in self.bug_type_extractors
            for keyword in extractor.keyword_prefixes
        }

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.Severity(),
            # Ignore keywords that would make the ML completely skewed
            # (we are going to use them as 100% rules in the evaluation phase).
            bug_features.Keywords(prefixes_to_ignore=label_keyword_prefixes),
            bug_features.IsCoverityIssue(),
            bug_features.HasCrashSignature(),
            bug_features.HasURL(),
            bug_features.HasW3CURL(),
            bug_features.HasGithubURL(),
            bug_features.Whiteboard(),
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

    def get_labels(self) -> tuple[dict[int, np.ndarray], list[str]]:
        classes = {}

        bug_map = {bug["id"]: bug for bug in bugzilla.get_bugs()}

        for bug_data in bug_map.values():
            target = np.zeros(len(self.bug_type_extractors))
            for i, is_type in enumerate(self.bug_type_extractors):
                if is_type(bug_data, bug_map):
                    target[i] = 1

            classes[int(bug_data["id"])] = target

        bug_types = [extractor.type_name for extractor in self.bug_type_extractors]

        for i, bug_type in enumerate(bug_types):
            logger.info(
                "%d %s bugs",
                sum(target[i] for target in classes.values()),
                bug_type,
            )

        return classes, bug_types

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()

    def overwrite_classes(
        self,
        bugs: Iterable[bugzilla.BugDict],
        classes: dict[int, np.ndarray],
        probabilities: bool,
    ):
        bug_map = {bug["id"]: bug for bug in bugs}

        for i, bug in enumerate(bugs):
            for j, is_type_applicable in enumerate(self.bug_type_extractors):
                if is_type_applicable(bug, bug_map):
                    if probabilities:
                        classes[i][j] = 1.0
                    else:
                        classes[i][j] = 1

        return classes
