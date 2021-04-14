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
            bug_features.has_str(),
            bug_features.has_regression_range(),
            bug_features.severity(),
            bug_features.has_crash_signature(),
            bug_features.has_url(),
            bug_features.whiteboard(),
            bug_features.product(),
            # TODO: We would like to use the component at the time of filing too,
            # but we can't because the rollback script doesn't support changes to
            # components yet.
            # bug_features.component(),
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
            f"Max fix time: {max(fix_time for bug_id, fix_time in bug_fix_times)}"
        )
        logger.info(f"Fix time quantiles: {quantiles}")
        logger.info(f"Fix time quartiles: {_quantiles(4)}")
        logger.info(f"Fix time deciles: {_quantiles(10)}")

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
                f"{sum(1 for label in classes.values() if label == i)} bugs are in the {i}th quantile"
            )

        return classes, list(range(len(quantiles) + 1))

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()
