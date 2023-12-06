# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import statistics

import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel

logger = logging.getLogger(__name__)

# TODO: Support modeling fix time at filing time or at assignment time
# TODO: Support modeling fix time for a subset of bugs (e.g. regressions only)
# TODO: Support modeling fix time with different number of quantiles


class FixTimeModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

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
            bug_features.Keywords(),
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
        bug_fix_times = []

        for bug in bugzilla.get_bugs():
            fix_time = bug_features.get_time_to_fix(bug)
            if fix_time is None:
                continue

            bug_fix_times.append((bug["id"], fix_time))

        def _quantiles(n):
            return statistics.quantiles(
                (fix_time for bug_id, fix_time in bug_fix_times), n=n
            )

        quantiles = _quantiles(2)

        logger.info(
            "Max fix time: %s", max(fix_time for bug_id, fix_time in bug_fix_times)
        )
        logger.info("Fix time quantiles: %s", quantiles)
        logger.info("Fix time quartiles: %s", _quantiles(4))
        logger.info("Fix time deciles: %s", _quantiles(10))

        classes = {}
        for bug_id, fix_time in bug_fix_times:
            for i, quantile in enumerate(quantiles):
                if fix_time <= quantile:
                    classes[bug_id] = i
                    break

            if bug_id not in classes:
                classes[bug_id] = i + 1

        for i in range(len(quantiles) + 1):
            logger.info(
                "%d bugs are in the %dth quantile",
                sum(label == i for label in classes.values()),
                i,
            )

        return classes, list(range(len(quantiles) + 1))

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()
