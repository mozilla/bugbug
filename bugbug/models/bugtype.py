# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from typing import Iterable, Optional

import numpy as np
import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel

logger = logging.getLogger(__name__)

KEYWORD_DICT = {
    "sec-": "security",
    "csectype-": "security",
    "memory-": "memory",
    "crash": "crash",
    "crashreportid": "crash",
    "perf": "performance",
    "topperf": "performance",
    "main-thread-io": "performance",
    "power": "power",
}
TYPE_LIST = sorted(set(KEYWORD_DICT.values()))


def bug_to_types(
    bug: bugzilla.BugDict, bug_map: Optional[dict[int, bugzilla.BugDict]] = None
) -> list[str]:
    types = set()

    if any(
        f"{whiteboard_text}" in bug["whiteboard"].lower()
        for whiteboard_text in ("overhead", "memshrink")
    ):
        types.add("memory")

    if "[power" in bug["whiteboard"].lower():
        types.add("power")

    if any(
        f"[{whiteboard_text}" in bug["whiteboard"].lower()
        for whiteboard_text in (
            "fxperf",
            "fxperfsize",
            "snappy",
            "pdfjs-c-performance",
            "pdfjs-performance",
        )
    ):
        types.add("performance")

    if "cf_performance" in bug and bug["cf_performance"] not in ("---", "?"):
        types.add("performance")

    if "cf_crash_signature" in bug and bug["cf_crash_signature"] not in ("", "---"):
        types.add("crash")

    if bug_map is not None:
        for bug_id in bug["blocks"]:
            if bug_id not in bug_map:
                continue

            alias = bug_map[bug_id]["alias"]
            if alias and alias.startswith("memshrink"):
                types.add("memory")

    for keyword_start, type in KEYWORD_DICT.items():
        if any(keyword.startswith(keyword_start) for keyword in bug["keywords"]):
            types.add(type)

    return list(types)


class BugTypeModel(BugModel):
    def __init__(self, lemmatization=False, historical=False):
        BugModel.__init__(self, lemmatization)

        self.calculate_importance = False

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.Severity(),
            # Ignore keywords that would make the ML completely skewed
            # (we are going to use them as 100% rules in the evaluation phase).
            bug_features.Keywords(set(KEYWORD_DICT.keys())),
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

    def get_labels(self) -> tuple[dict[int, np.ndarray], list[str]]:
        classes = {}

        bug_map = {bug["id"]: bug for bug in bugzilla.get_bugs()}

        for bug_data in bug_map.values():
            target = np.zeros(len(TYPE_LIST))
            for type_ in bug_to_types(bug_data, bug_map):
                target[TYPE_LIST.index(type_)] = 1

            classes[int(bug_data["id"])] = target

        for type_ in TYPE_LIST:
            logger.info(
                f"{sum(1 for target in classes.values() if target[TYPE_LIST.index(type_)] == 1)} {type_} bugs"
            )

        return classes, TYPE_LIST

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names_out()

    def overwrite_classes(
        self,
        bugs: Iterable[bugzilla.BugDict],
        classes: dict[int, np.ndarray],
        probabilities: bool,
    ):
        for i, bug in enumerate(bugs):
            for type_ in bug_to_types(bug):
                if probabilities:
                    classes[i][TYPE_LIST.index(type_)] = 1.0
                else:
                    classes[i][TYPE_LIST.index(type_)] = 1

        return classes
