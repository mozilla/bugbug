# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

import xgboost
from imblearn.over_sampling import SMOTE
from imblearn.pipeline import Pipeline as ImblearnPipeline
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bugzilla, comment_features, feature_cleanup, utils
from bugbug.model import CommentModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SAFE_DOMAINS = {"github.com", "mozilla.com", "mozilla.org"}


class SpamCommentModel(CommentModel):
    def __init__(self, lemmatization=True):
        CommentModel.__init__(self, lemmatization)

        self.calculate_importance = False

        feature_extractors = [
            comment_features.NumberOfLinks(SAFE_DOMAINS),
            comment_features.WordCount(),
            comment_features.HourOfDay(),
            comment_features.DayOfYear(),
            comment_features.WeekOfYear(),
            comment_features.Weekday(),
            comment_features.UnknownLinkAtBeginning(SAFE_DOMAINS),
            comment_features.UnknownLinkAtEnd(SAFE_DOMAINS),
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
                    SMOTE(random_state=0),
                ),
                (
                    "estimator",
                    xgboost.XGBClassifier(
                        n_jobs=utils.get_physical_cpu_count(),
                        learning_rate=0.01,
                        n_estimators=1000,
                    ),
                ),
            ]
        )

    @staticmethod
    def __download_older_bugs_with_spam_comments() -> None:
        """Retrieve older bugs within the past specified number of months which have spam comments.

        This function provides an option to extend the dataset used for model training by including older spam comments.
        """
        params = {
            "f1": "comment_tag",
            "o1": "substring",
            "v1": "spam",
            "product": bugzilla.PRODUCTS,
        }

        logger.info("Downloading older bugs...")
        bugs_ids = bugzilla.get_ids(params)
        older_bugs = bugzilla.download_bugs(bugs_ids)

        logger.info("%d older bugs have been downloaded.", len(older_bugs))

    def get_labels(self):
        classes = {}

        self.__download_older_bugs_with_spam_comments()

        for bug in bugzilla.get_bugs():
            for comment in bug["comments"]:
                comment_id = comment["id"]

                # Skip the first comment because most first comments may contain links.
                # Skip comments filed by Mozillians and bots, since we are sure they are not spam.
                # Skip comments whose text has been removed.
                if any(
                    [
                        comment["count"] == "0",
                        "@mozilla" in comment["creator"],
                        "(comment removed)" in comment["text"],
                    ]
                ):
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
        return (
            (comment, classes[comment["id"]])
            for bug in bugzilla.get_bugs()
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
