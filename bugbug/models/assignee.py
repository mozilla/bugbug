# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from collections import Counter

import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel

MINIMUM_ASSIGNMENTS = 5
ADDRESSES_TO_EXCLUDE = [
    "nobody@bugzilla.org",
    "nobody@example.com",
    "nobody@fedoraproject.org",
    "nobody@mozilla.org",
    "nobody@msg1.fake",
    "nobody@nss.bugs",
    "nobody@t4b.me",
]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AssigneeModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

        self.cross_validation_enabled = False
        self.calculate_importance = False

        feature_extractors = [
            bug_features.has_str(),
            bug_features.severity(),
            bug_features.keywords(),
            bug_features.is_coverity_issue(),
            bug_features.has_crash_signature(),
            bug_features.has_url(),
            bug_features.has_w3c_url(),
            bug_features.has_github_url(),
            bug_features.whiteboard(),
            bug_features.patches(),
            bug_features.landings(),
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
                        feature_extractors,
                        cleanup_functions,
                        rollback=True,
                        rollback_when=self.rollback,
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

    def get_labels(self):
        classes = {}

        for bug_data in bugzilla.get_bugs():
            if bug_data["assigned_to_detail"]["email"] in ADDRESSES_TO_EXCLUDE:
                continue

            bug_id = int(bug_data["id"])
            classes[bug_id] = bug_data["assigned_to_detail"]["email"]

        assignee_counts = Counter(classes.values()).most_common()
        top_assignees = set(
            assignee
            for assignee, count in assignee_counts
            if count > MINIMUM_ASSIGNMENTS
        )

        logger.info("%d assignees", len(top_assignees))
        for assignee, count in assignee_counts:
            logger.info("%s: %d", assignee, count)

        classes = {
            bug_id: assignee
            for bug_id, assignee in classes.items()
            if assignee in top_assignees
        }

        return classes, set(classes.values())

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names_out()

    def rollback(self, change):
        return change["field_name"].startswith("assigned_to")
