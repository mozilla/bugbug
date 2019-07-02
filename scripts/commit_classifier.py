# -*- coding: utf-8 -*-

import argparse
import io
import os
from logging import INFO, basicConfig, getLogger

import hglib

from bugbug import db, repository
from bugbug.models.regressor import RegressorModel

basicConfig(level=INFO)
logger = getLogger(__name__)


class CommitClassifier(object):
    def __init__(self, cache_root):
        self.cache_root = cache_root

        assert os.path.isdir(cache_root), f"Cache root {cache_root} is not a dir."
        self.repo_dir = os.path.join(cache_root, "mozilla-central")

        self.model = RegressorModel.load("regressormodel")

    def update_commit_db(self):
        repository.clone(self.repo_dir)

        db.download_version(repository.COMMITS_DB)
        if db.is_old_version(repository.COMMITS_DB) or not os.path.exists(
            repository.COMMITS_DB
        ):
            db.download(repository.COMMITS_DB, force=True, support_files_too=True)

        for commit in repository.get_commits():
            pass

        rev_start = "children({})".format(commit["node"])

        repository.download_commits(self.repo_dir, rev_start)

    def classify(self, message, patch):
        self.update_commit_db()

        with hglib.open(self.repo_dir) as hg:
            # Apply patch.
            hg.import_(
                patches=io.BytesIO(patch.encode("utf-8")),
                message=message,
                user="bugbug",
            )

            patch_rev = hg.log(limit=1)[0].node

            # Analyze patch.
            commits = repository.download_commits(
                self.repo_dir, rev_start=patch_rev.decode("utf-8"), ret=True, save=False
            )

        probs, importance = self.model.classify(
            commits[0], probabilities=True, importances=True
        )

        feature_names = self.model.get_feature_names()

        features = []
        for i, (val, feature_index, is_positive) in enumerate(
            importance["importances"]
        ):
            features.append(
                [
                    i + 1,
                    feature_names[int(feature_index)],
                    f'({"+" if (is_positive) else "-"}{val})',
                ]
            )

        print(probs)
        print(features)


if __name__ == "__main__":
    description = "Classify a commit"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("cache-root", help="Cache for repository clones.")
    parser.add_argument("patch", help="Patch to analyze.")

    args = parser.parse_args()

    classifier = CommitClassifier(getattr(args, "cache-root"))

    with open(args.patch) as f:
        patch = f.read()

    # TODO: Use commit message from the patch.
    classifier.classify("Bug 1 - Test", patch)
