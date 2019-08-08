# -*- coding: utf-8 -*-

import argparse
import io
import json
import os
from logging import INFO, basicConfig, getLogger

import hglib
from libmozdata.phabricator import PhabricatorAPI

from bugbug import db, repository
from bugbug.models.regressor import RegressorModel
from bugbug.utils import download_check_etag, get_secret, zstd_decompress

basicConfig(level=INFO)
logger = getLogger(__name__)

URL = "https://index.taskcluster.net/v1/task/project.relman.bugbug.train_regressor.latest/artifacts/public/regressormodel.zst"


class CommitClassifier(object):
    def __init__(self, cache_root):
        self.cache_root = cache_root

        assert os.path.isdir(cache_root), f"Cache root {cache_root} is not a dir."
        self.repo_dir = os.path.join(cache_root, "mozilla-central")

        if not os.path.exists("regressormodel"):
            download_check_etag(URL, "regressormodel.zst")
            zstd_decompress("regressormodel")
            assert os.path.exists("regressormodel"), "Decompressed file exists"

        self.model = RegressorModel.load("regressormodel")

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

        # Load all the diffs revisions
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

            if patch.commits:
                message = patch.commits[0]["message"]
            else:
                message = revisions[patch.phid]["fields"]["title"]

            logger.info(f"Applying {patch.phid}: {message}")
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

        probs, importance = self.model.classify(
            commits[-1], probabilities=True, importances=True
        )

        features = []
        for i, (val, feature_index, is_positive) in enumerate(
            importance["importances"]["classes"][1][0]
        ):
            features.append(
                [
                    i + 1,
                    importance["feature_legend"][str(i + 1)],
                    f'{"+" if (is_positive) else "-"}{val}',
                ]
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
