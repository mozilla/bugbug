# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from typing import Optional

import xgboost
from imblearn.pipeline import Pipeline as ImblearnPipeline
from imblearn.under_sampling import RandomUnderSampler
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.pipeline import Pipeline
from sklearn.utils.class_weight import compute_sample_weight

from bugbug import (
    commit_features,
    db,
    feature_cleanup,
    repository,
    test_scheduling,
    utils,
)
from bugbug.model import CommitModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class _BalancedXGBClassifier(xgboost.XGBClassifier):
    def fit(self, X, y, **fit_params):
        fit_params["sample_weight"] = compute_sample_weight("balanced", y)
        return super().fit(X, y, **fit_params)


PLATFORM_KEYWORDS = (
    (("linux",), "linux"),
    (("windows", "win"), "windows"),
    (("android", "apk", "fenix", "focus", "klar"), "android"),
    (("macosx",), "mac"),
)


def get_platform(task_name: str) -> Optional[str]:
    config = task_name.split("/")[0]
    for keywords, platform in PLATFORM_KEYWORDS:
        if any(k in config for k in keywords):
            return platform
    return None


class TestFailureModel(CommitModel):
    def __init__(self, lemmatization=False):
        CommitModel.__init__(self, lemmatization)

        self.training_dbs.append(test_scheduling.TEST_LABEL_SCHEDULING_DB)

        feature_extractors = [
            commit_features.SourceCodeFileSize(),
            commit_features.OtherFileSize(),
            commit_features.TestFileSize(),
            commit_features.SourceCodeAdded(),
            commit_features.OtherAdded(),
            commit_features.TestAdded(),
            commit_features.SourceCodeDeleted(),
            commit_features.OtherDeleted(),
            commit_features.TestDeleted(),
            commit_features.ReviewersNum(),
            commit_features.Types(),
            commit_features.Files(),
            commit_features.Components(),
            commit_features.ComponentsModifiedNum(),
            commit_features.Directories(),
            commit_features.DirectoriesModifiedNum(),
            commit_features.SourceCodeFilesModifiedNum(),
            commit_features.OtherFilesModifiedNum(),
            commit_features.TestFilesModifiedNum(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "commit_extractor",
                    commit_features.CommitExtractor(feature_extractors, []),
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
                                "files",
                                CountVectorizer(
                                    analyzer=utils.keep_as_is,
                                    lowercase=False,
                                    min_df=0.005,
                                ),
                                "files",
                            ),
                        ]
                    ),
                ),
                ("sampler", RandomUnderSampler(random_state=0)),
                (
                    "estimator",
                    xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count()),
                ),
            ]
        )

    def items_gen(self, classes):
        commit_map = {}

        for commit in repository.get_commits():
            commit_map[commit["node"]] = commit

        assert len(commit_map) > 0

        for revs, test_datas in test_scheduling.get_test_scheduling_history("label"):
            if revs[0] not in classes:
                continue

            commits = tuple(
                commit_map[revision] for revision in revs if revision in commit_map
            )
            if len(commits) == 0:
                continue

            commit_data = commit_features.merge_commits(commits)
            yield commit_data, classes[revs[0]]

    def get_labels(self):
        classes = {}

        for revs, test_datas in test_scheduling.get_test_scheduling_history("label"):
            rev = revs[0]

            if any(
                test_data["is_likely_regression"] or test_data["is_possible_regression"]
                for test_data in test_datas
            ):
                classes[rev] = 1
            else:
                classes[rev] = 0

        logger.info("%d commits failed", sum(label == 1 for label in classes.values()))
        logger.info(
            "%d commits did not fail",
            sum(label == 0 for label in classes.values()),
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()


class TestConfigModel(CommitModel):
    def train_test_split(self, X, y):
        from sklearn.model_selection import train_test_split

        return train_test_split(X, y, test_size=0.1, random_state=0)

    def __init__(self, lemmatization=False):
        CommitModel.__init__(self, lemmatization)

        self.training_dbs.append(test_scheduling.TEST_LABEL_SCHEDULING_DB)
        self.training_dbs.append(test_scheduling.PUSH_DATA_CONFIG_GROUP_DB)

        feature_extractors = [
            commit_features.SourceCodeFileSize(),
            commit_features.OtherFileSize(),
            commit_features.TestFileSize(),
            commit_features.SourceCodeAdded(),
            commit_features.OtherAdded(),
            commit_features.TestAdded(),
            commit_features.SourceCodeDeleted(),
            commit_features.OtherDeleted(),
            commit_features.TestDeleted(),
            commit_features.Types(),
            commit_features.TypesCounts(),
            commit_features.FilesPathComponents(),
            commit_features.Components(),
            commit_features.ComponentsModifiedNum(),
            commit_features.DirectoriesModifiedNum(),
            commit_features.SourceCodeFilesModifiedNum(),
            commit_features.OtherFilesModifiedNum(),
            commit_features.TestFilesModifiedNum(),
            commit_features.SourceCodeFileMetrics(),
            commit_features.PlatformKeywords("mac", ["cocoa", "mac"]),
            commit_features.PlatformKeywords(
                "windows", ["wmf", "winlauncher", "dxgi", "hresult", "playready"]
            ),
            commit_features.PlatformKeywords("linux", ["vulkan", "wayland"]),
            commit_features.PlatformKeywords("android", ["geckoview", "fenix"]),
        ]

        cleanup_functions = [
            feature_cleanup.fileref(),
            feature_cleanup.url(),
            feature_cleanup.synonyms(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "commit_extractor",
                    commit_features.CommitExtractor(
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
                                "filespathcomponents",
                                CountVectorizer(
                                    analyzer=utils.keep_as_is,
                                    lowercase=False,
                                    min_df=0.01,
                                ),
                                "filespathcomponents",
                            ),
                            (
                                "desc",
                                self.text_vectorizer(min_df=0.01, stop_words="english"),
                                "desc",
                            ),
                        ]
                    ),
                ),
                (
                    "estimator",
                    _BalancedXGBClassifier(
                        learning_rate=0.05,
                        n_estimators=200,
                        subsample=1.0,
                        colsample_bytree=0.7,
                        reg_alpha=0.5,
                        max_depth=4,
                        n_jobs=utils.get_physical_cpu_count(),
                    ),
                ),
            ]
        )

    def _get_label_db_data(self):
        all_revs = set()
        platforms = {}
        for revs, test_datas in test_scheduling.get_test_scheduling_history("label"):
            rev = revs[0]
            all_revs.add(rev)
            has_likely = any(td["is_likely_regression"] for td in test_datas)
            failing_platforms = set()
            for td in test_datas:
                if has_likely:
                    if not td["is_likely_regression"]:
                        continue
                else:
                    if not td["is_possible_regression"]:
                        continue
                if "-test-verify" in td["name"]:
                    continue
                platform = get_platform(td["name"])
                if platform is not None:
                    failing_platforms.add(platform)
            if failing_platforms:
                platforms[rev] = failing_platforms
        return all_revs, platforms

    def _get_cg_db_data(self):
        all_revs = set()
        platforms = {}
        for revisions, _, _, possible_regressions, likely_regressions in db.read(
            test_scheduling.PUSH_DATA_CONFIG_GROUP_DB
        ):
            rev = revisions[0]
            all_revs.add(rev)
            regressions = (
                likely_regressions if likely_regressions else possible_regressions
            )
            failing_platforms = set()
            for config, group in regressions:
                platform = get_platform(config)
                if platform is not None:
                    failing_platforms.add(platform)
            if failing_platforms:
                platforms[rev] = failing_platforms
        return all_revs, platforms

    def _get_rev_to_revisions(self, classes):
        rev_to_revisions = {}
        for revs, _ in test_scheduling.get_test_scheduling_history("label"):
            if revs[0] in classes:
                rev_to_revisions[revs[0]] = revs
        for revisions, _, _, _, _ in db.read(test_scheduling.PUSH_DATA_CONFIG_GROUP_DB):
            if revisions[0] in classes and revisions[0] not in rev_to_revisions:
                rev_to_revisions[revisions[0]] = revisions
        return rev_to_revisions

    def get_labels(self):
        label_all_revs, label_db_platforms = self._get_label_db_data()
        cg_all_revs, cg_db_platforms = self._get_cg_db_data()

        both_analyzed = label_all_revs & cg_all_revs

        classes = {}
        for rev in both_analyzed:
            platforms = label_db_platforms.get(rev, set()) | cg_db_platforms.get(
                rev, set()
            )
            if not platforms:
                continue
            classes[rev] = next(iter(platforms)) if len(platforms) == 1 else "any"

        class_names = sorted(set(classes.values()))

        logger.info("%d pushes considered", len(classes))
        for label in class_names:
            logger.info(
                "%d pushes with label '%s'",
                sum(1 for lbl in classes.values() if lbl == label),
                label,
            )

        return classes, class_names

    def items_gen(self, classes):
        rev_to_revisions = self._get_rev_to_revisions(classes)

        needed_revisions = set()
        for revisions in rev_to_revisions.values():
            needed_revisions.update(revisions)

        commit_map = {}
        for commit in repository.get_commits():
            if commit["node"] in needed_revisions:
                commit_map[commit["node"]] = commit

        assert len(commit_map) > 0

        for rev, revisions in rev_to_revisions.items():
            commits = tuple(commit_map[r] for r in revisions if r in commit_map)
            if not commits:
                continue
            commit_data = commit_features.merge_commits(commits)
            yield commit_data, classes[rev]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()
