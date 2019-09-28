# -*- coding: utf-8 -*-

import argparse
import io
import json
import os
from logging import INFO, basicConfig, getLogger

import hglib
import joblib
import numpy as np
from libmozdata.phabricator import PhabricatorAPI
from scipy.stats import spearmanr

from bugbug import db, repository
from bugbug.models.regressor import RegressorModel
from bugbug.utils import download_check_etag, get_secret, to_array, zstd_decompress

basicConfig(level=INFO)
logger = getLogger(__name__)

URL = "https://index.taskcluster.net/v1/task/project.relman.bugbug.train_regressor.latest/artifacts/public/{}"


class CommitClassifier(object):
    def __init__(self, cache_root):
        self.cache_root = cache_root

        assert os.path.isdir(cache_root), f"Cache root {cache_root} is not a dir."
        self.repo_dir = os.path.join(cache_root, "mozilla-central")

        regressormodel_path = "regressormodel"
        if not os.path.exists(regressormodel_path):
            download_check_etag(
                URL.format(f"{regressormodel_path}.zst"), f"{regressormodel_path}.zst"
            )
            zstd_decompress(regressormodel_path)
            assert os.path.exists(regressormodel_path), "Decompressed model exists"

        regressormodel_data_X_path = "regressormodel_data_X"
        if not os.path.exists(regressormodel_data_X_path):
            download_check_etag(
                URL.format(f"{regressormodel_data_X_path}.zst"),
                f"{regressormodel_data_X_path}.zst",
            )
            zstd_decompress(regressormodel_data_X_path)
            assert os.path.exists(
                regressormodel_data_X_path
            ), "Decompressed X dataset exists"

        regressormodel_data_y_path = "regressormodel_data_y"
        if not os.path.exists(regressormodel_data_y_path):
            download_check_etag(
                URL.format(f"{regressormodel_data_y_path}.zst"),
                f"{regressormodel_data_y_path}.zst",
            )
            zstd_decompress(regressormodel_data_y_path)
            assert os.path.exists(
                regressormodel_data_y_path
            ), "Decompressed y dataset exists"

        self.model = RegressorModel.load(regressormodel_path)
        self.X = to_array(joblib.load(regressormodel_data_X_path))
        self.y = to_array(joblib.load(regressormodel_data_y_path))

    def update_commit_db(self):
        repository.clone(self.repo_dir)

        if db.is_old_version(repository.COMMITS_DB) or not db.exists(
            repository.COMMITS_DB
        ):
            db.download(repository.COMMITS_DB, force=True, support_files_too=True)

        for commit in repository.get_commits():
            pass

        rev_start = "children({})".format(commit["node"])

        repository.download_commits(self.repo_dir, rev_start)

    def apply_phab(self, hg, diff_id):
        def has_revision(revision):
            if not revision:
                return False
            try:
                hg.identify(revision)
                return True
            except hglib.error.CommandError:
                return False

        phabricator_api = PhabricatorAPI(
            api_key=get_secret("PHABRICATOR_TOKEN"), url=get_secret("PHABRICATOR_URL")
        )

        # Get the stack of patches
        stack = phabricator_api.load_patches_stack(diff_id)
        assert len(stack) > 0, "No patches to apply"

        # Find the first unknown base revision
        needed_stack = []
        revisions = {}
        for patch in reversed(stack):
            needed_stack.insert(0, patch)

            # Stop as soon as a base revision is available
            if has_revision(patch.base_revision):
                logger.info(
                    f"Stopping at diff {patch.id} and revision {patch.base_revision}"
                )
                break

        if not needed_stack:
            logger.info("All the patches are already applied")
            return

        # Load all the diff revisions
        diffs = phabricator_api.search_diffs(diff_phid=[p.phid for p in stack])
        revisions = {
            diff["phid"]: phabricator_api.load_revision(rev_phid=diff["revisionPHID"])
            for diff in diffs
        }

        # Update repo to base revision
        hg_base = needed_stack[0].base_revision
        if hg_base:
            hg.update(rev=hg_base, clean=True)
            logger.info(f"Updated repo to {hg_base}")

        for patch in needed_stack:
            revision = revisions[patch.phid]

            if patch.commits:
                message = patch.commits[0]["message"]
            else:
                message = revision["fields"]["title"]

            logger.info(
                f"Applying {patch.phid} from revision {revision['id']}: {message}"
            )

            hg.import_(
                patches=io.BytesIO(patch.patch.encode("utf-8")),
                message=message,
                user="bugbug",
            )

    def classify(self, diff_id):
        self.update_commit_db()

        with hglib.open(self.repo_dir) as hg:
            self.apply_phab(hg, diff_id)

            patch_rev = hg.log(revrange="not public()")[0].node

            # Analyze patch.
            commits = repository.download_commits(
                self.repo_dir, rev_start=patch_rev.decode("utf-8"), save=False
            )

        # We use "clean" commits as the background dataset for feature importance.
        # This way, we can see the features which are most important in differentiating
        # the current commit from the "clean" commits.
        background_dataset = self.X[self.y == 0]

        probs, importance = self.model.classify(
            commits[-1],
            probabilities=True,
            importances=True,
            background_dataset=background_dataset,
            importance_cutoff=0.1,
        )

        features = []
        for i, (val, feature_index, is_positive) in enumerate(
            importance["importances"]["classes"][1][0]
        ):
            value = importance["importances"]["values"][0, int(feature_index)]

            X = self.X[:, int(feature_index)]
            spearman = spearmanr(X, self.y)

            buggy_X = X[self.y == 1]
            clean_X = X[self.y == 0]
            median = np.median(X)
            median_clean = np.median(clean_X)
            median_buggy = np.median(buggy_X)

            perc_buggy_values_higher_than_median = (
                buggy_X > median
            ).sum() / buggy_X.shape[0]
            perc_buggy_values_lower_than_median = (
                buggy_X < median
            ).sum() / buggy_X.shape[0]
            perc_clean_values_higher_than_median = (
                clean_X > median
            ).sum() / clean_X.shape[0]
            perc_clean_values_lower_than_median = (
                clean_X < median
            ).sum() / clean_X.shape[0]

            logger.info("Feature: {}".format(importance["feature_legend"][str(i + 1)]))
            logger.info("Shap value: {}{}".format("+" if (is_positive) else "-", val))
            logger.info(f"spearman:  {spearman}")
            logger.info(f"value: {value}")
            logger.info(f"overall mean: {np.mean(X)}")
            logger.info(f"overall median: {np.median(X)}")
            logger.info(f"mean for y == 0: {np.mean(clean_X)}")
            logger.info(f"mean for y == 1: {np.mean(buggy_X)}")
            logger.info(f"median for y == 0: {np.median(clean_X)}")
            logger.info(f"median for y == 1: {np.median(buggy_X)}")
            logger.info(
                f"perc_buggy_values_higher_than_median: {perc_buggy_values_higher_than_median}"
            )
            logger.info(
                f"perc_buggy_values_lower_than_median: {perc_buggy_values_lower_than_median}"
            )
            logger.info(
                f"perc_clean_values_higher_than_median: {perc_clean_values_higher_than_median}"
            )
            logger.info(
                f"perc_clean_values_lower_than_median: {perc_clean_values_lower_than_median}"
            )

            features.append(
                {
                    "index": i + 1,
                    "name": importance["feature_legend"][str(i + 1)],
                    "shap": f'{"+" if (is_positive) else "-"}{val}',
                    "value": importance["importances"]["values"][0, int(feature_index)],
                    "spearman": spearman,
                    "median": median,
                    "median_bug_introducing": median_buggy,
                    "median_clean": median_clean,
                    "perc_buggy_values_higher_than_median": perc_buggy_values_higher_than_median,
                    "perc_buggy_values_lower_than_median": perc_buggy_values_lower_than_median,
                    "perc_clean_values_higher_than_median": perc_clean_values_higher_than_median,
                    "perc_clean_values_lower_than_median": perc_clean_values_lower_than_median,
                }
            )

        with open("probs.json", "w") as f:
            json.dump(probs[0].tolist(), f)

        with open("importances.json", "w") as f:
            json.dump(features, f)

        with open("importance.html", "w") as f:
            f.write(importance["html"])


def main():
    description = "Classify a commit"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("cache-root", help="Cache for repository clones.")
    parser.add_argument("diff_id", help="diff ID to analyze.", type=int)

    args = parser.parse_args()

    classifier = CommitClassifier(getattr(args, "cache-root"))
    classifier.classify(args.diff_id)


if __name__ == "__main__":
    main()
