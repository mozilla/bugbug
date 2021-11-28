# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from typing import Iterable, List, Optional, Tuple
from __future__ import annotations

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
    "sec-critical": "security",
    "sec-high": "security",
    "sec-moderate": "security",
    "sec-low": "security",
    "sec-other": "security",
    "sec-audit": "security",
    "sec-vector": "security",
    "sec-want": "security",
    "csectype-bounds": "security",
    "csectype-disclosure": "security",
    "csectype-dos": "security",
    "csectype-framepoisoning": "security",
    "csectype-intoverflow": "security",
    "csectype-jit": "security",
    "csectype-nullptr": "security",
    "csectype-oom": "security",
    "csectype-other": "security",
    "csectype-priv-escalation": "security",
    "csectype-race": "security",
    "csectype-sop": "security",
    "csectype-spoof": "security",
    "csectype-uaf": "security",
    "csectype-undefined": "security",
    "csectype-uninitialized": "security",
    "csectype-wildptr": "security",
    "memory-footprint": "memory",
    "memory-leak": "memory",
    "crash": "crash",
    "crashreportid": "crash",
    "perf": "performance",
    "topperf": "performance",
    "power": "power",
}
TYPE_LIST = sorted(set(KEYWORD_DICT.values()))


def bug_to_types(
    bug: bugzilla.BugDict, bug_map: Optional[dict[int, bugzilla.BugDict]] = None
) -> List[str]:
    types = set()

    if "[overhead" in bug["whiteboard"].lower():
        types.add("memory")

    if "[power" in bug["whiteboard"].lower():
        types.add("power")

    if any(
        f"[{whiteboard_text}" in bug["whiteboard"].lower()
        for whiteboard_text in ("fxperf", "snappy")
    ):
        types.add("perf")

    if "cf_crash_signature" in bug and bug["cf_crash_signature"] not in ("", "---"):
        types.add("crash")

    if bug_map is not None:
        for bug_id in bug["blocks"]:
            if bug_id not in bug_map:
                continue

            alias = bug_map[bug_id]["alias"]
            if alias and alias.startswith("memshrink"):
                types.add("memory")

    return list(
        types.union(
            set(
                KEYWORD_DICT[keyword]
                for keyword in bug["keywords"]
                if keyword in KEYWORD_DICT
            )
        )
    )


class BugTypeModel(BugModel):
    def __init__(self, lemmatization=False, historical=False):
        BugModel.__init__(self, lemmatization)

        self.calculate_importance = False

        feature_extractors = [
            bug_features.has_str(),
            bug_features.severity(),
            # Ignore keywords that would make the ML completely skewed
            # (we are going to use them as 100% rules in the evaluation phase).
            bug_features.keywords(set(KEYWORD_DICT.keys())),
            bug_features.is_coverity_issue(),
            bug_features.has_crash_signature(),
            bug_features.has_url(),
            bug_features.has_w3c_url(),
            bug_features.has_github_url(),
            bug_features.whiteboard(),
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

    def get_labels(self) -> Tuple[dict[int, np.ndarray], List[str]]:
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
        return self.extraction_pipeline.named_steps["union"].get_feature_names()

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
