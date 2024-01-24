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

from bugbug import bugzilla, comment_features, feature_cleanup, utils
from bugbug.model import CommentModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SpamCommentModel(CommentModel):
    def __init__(self, lemmatization=True):
        CommentModel.__init__(self, lemmatization)

        self.calculate_importance = False

        feature_extractors = [
            comment_features.CommenterExperience(),
            comment_features.CommentHasLink(),
            comment_features.CommentTextHasKeywords(
                {"free", "win", "discount", "limited time", "casino", "rent"}
            ),
        ]

        cleanup_functions = [
            feature_cleanup.fileref(),
            feature_cleanup.url(),
            feature_cleanup.synonyms(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "comment_extractor",
                    comment_features.CommentExtractor(
                        feature_extractors, cleanup_functions
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
                            (
                                "comment_text",
                                self.text_vectorizer(min_df=0.0001),
                                "comment_text",
                            ),
                        ]
                    ),
                ),
                (
                    "sampler",
                    RandomUnderSampler(
                        random_state=0, sampling_strategy="not minority"
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

        for bug in bugzilla.get_bugs(include_invalid=True):
            for comment in bug["comments"]:
                comment_id = comment["id"]

                # Skip comments filed by Mozillians and bots, since we are sure they are not spam.
                if "@mozilla" in comment["creator"]:
                    continue

                if "spam" in comment["tags"]:
                    classes[comment_id] = 1
                else:
                    classes[comment_id] = 0

        logger.info(
            "%d comments are classified as non-spam",
            sum(label == 0 for label in classes.values()),
        )
        logger.info(
            "%d comments are classified as spam",
            sum(label == 1 for label in classes.values()),
        )

        return classes, [0, 1]

    def items_gen(self, classes):
        # Overwriting this method to add include_invalid=True to get_bugs to
        # include spam bugs which have a number of spam comments.
        return (
            (comment, classes[comment["id"]])
            for bug in bugzilla.get_bugs(include_invalid=True)
            for comment in bug["comments"]
            if comment["id"] in classes
        )

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()

    def overwrite_classes(self, comments, classes, probabilities):
        for i, comment in enumerate(comments):
            if "@mozilla" in comment["creator"]:
                if probabilities:
                    classes[i] = [1.0, 0.0]
                else:
                    classes[i] = 0

        return classes
