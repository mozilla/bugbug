# -*- coding: utf-8 -*-

import argparse
import io
import json
import os
from logging import INFO, basicConfig, getLogger

import hglib
import zstandard
from libmozdata.phabricator import PhabricatorAPI

from bugbug import db, repository
from bugbug.models.regressor import RegressorModel
from bugbug.utils import download_check_etag, get_secret

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
            dctx = zstandard.ZstdDecompressor()
            with open("regressormodel.zst", "rb") as input_f:
                with open("regressormodel", "wb") as output_f:
                    dctx.copy_stream(input_f, output_f)
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
        phabricator_api = PhabricatorAPI(
            api_key=get_secret("PHABRICATOR_TOKEN"), url=get_secret("PHABRICATOR_URL")
        )

        diffs = phabricator_api.search_diffs(diff_id=diff_id)
        assert len(diffs) == 1, f"No diff available for {diff_id}"
        diff = diffs[0]

        # Get the stack of patches
        base, patches = phabricator_api.load_patches_stack(hg, diff)
        assert len(patches) > 0, "No patches to apply"

        # Load all the diffs details with commits messages
        diffs = phabricator_api.search_diffs(
            diff_phid=[p[0] for p in patches], attachments={"commits": True}
        )

        diffs_data = {}
        for diff in diffs:
            revision = phabricator_api.load_revision(rev_phid=diff["revisionPHID"])
            logger.info(
                "Diff {} linked to Revision {}".format(diff["id"], revision["id"])
            )

            diffs_data[diff["phid"]] = {
                "commits": diff["attachments"]["commits"].get("commits", []),
                "revision": revision,
            }

        # First apply patches on local repo
        for diff_phid, patch in patches:
            diff_data = diffs_data.get(diff_phid)

            commits = diff_data["commits"]
            revision = diff_data["revision"]

            if commits and commits[0]["message"]:
                message = commits[0]["message"]
            else:
                message = revision["fields"]["title"]

            logger.info(f"Applying {diff_phid}")
            hg.import_(
                patches=io.BytesIO(patch.encode("utf-8")),
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
