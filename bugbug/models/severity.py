# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from datetime import datetime, timezone

import dateutil.parser
from dateutil.relativedelta import relativedelta
from imblearn.pipeline import Pipeline as ImblearnPipeline
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup
from bugbug.model import BugModel

HIGH_SEVERITY = {"S1", "S2"}


class SeverityModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

        self.calculate_importance = False

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.HasRegressionRange(),
            bug_features.Keywords(),
            bug_features.HasCrashSignature(),
            bug_features.HasURL(),
            bug_features.Whiteboard(),
            bug_features.Product(),
            bug_features.Component(),
            bug_features.Version(),
            bug_features.HasAttachment(),
            bug_features.Platform(),
            bug_features.OpSys(),
            bug_features.BlockedBugsNumber(),
            bug_features.NumberOfBugDependencies(),
            bug_features.CCNumber(),
            bug_features.CommentCount(),
            bug_features.CommentLength(),
            bug_features.NumWordsTitle(),
            bug_features.NumWordsComments(),
            bug_features.EverAffected(),
            bug_features.IsCoverityIssue(),
            bug_features.HasImageAttachment(),
            bug_features.FiledVia(),
            bug_features.IsSecurityBug(),
            bug_features.IsCrashBug(),
            # bug_features.ReporterExperience(),
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

        # Logistic regression generalizes much better than XGBoost on high-dimensional
        # sparse TF-IDF features. class_weight='balanced' automatically handles the
        # 10:1 class imbalance. C=0.5 provides regularization that prevents overfitting
        # on bug-specific text patterns. The unscaled structured features act as
        # implicit downweighting for text features, which helps generalization.
        self.clf = ImblearnPipeline(
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
                    LogisticRegression(
                        solver="lbfgs",
                        max_iter=2000,
                        class_weight="balanced",
                        C=0.5,
                        n_jobs=-1,
                    ),
                ),
            ]
        )

    def get_labels(self):
        classes = {}

        FIRST_DATE = datetime.now(timezone.utc) - relativedelta(years=2)

        for bug_data in bugzilla.get_bugs(include_invalid=True):
            if bug_data["type"] != "defect":
                continue

            if bug_data["severity"] not in ("S1", "S2", "S3", "S4"):
                continue

            # Skip bugs filed by Mozillians or SoftVision.
            if (
                "@mozilla" in bug_data["creator"]
                or "@softvision" in bug_data["creator"]
            ):
                continue

            if dateutil.parser.parse(bug_data["creation_time"]) < FIRST_DATE:
                continue

            bug_id = bug_data["id"]

            # Skip bugs that have a severity set since the beginning.
            if not any(
                change["field_name"] == "severity"
                for history in bug_data["history"]
                for change in history["changes"]
            ):
                continue

            label = "S2" if bug_data["severity"] in HIGH_SEVERITY else "not_S2"
            classes[bug_id] = label

        print(
            "{} S2 bugs, {} not-S2 bugs".format(
                sum(1 for label in classes.values() if label == "S2"),
                sum(1 for label in classes.values() if label == "not_S2"),
            )
        )

        return classes, {"S2", "not_S2"}

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()
