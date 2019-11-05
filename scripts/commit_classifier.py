# -*- coding: utf-8 -*-

import argparse
import csv
import io
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from logging import INFO, basicConfig, getLogger

import hglib
import joblib
import numpy as np
from dateutil.relativedelta import relativedelta
from libmozdata import vcs_map
from libmozdata.phabricator import PhabricatorAPI
from scipy.stats import spearmanr

from bugbug import db, repository
from bugbug.models.regressor import RegressorModel
from bugbug.utils import (
    download_check_etag,
    get_secret,
    retry,
    to_array,
    zstd_decompress,
)

basicConfig(level=INFO)
logger = getLogger(__name__)

URL = "https://index.taskcluster.net/v1/task/project.relman.bugbug.train_regressor.latest/artifacts/public/{}"


# ------------------------------------------------------------------------------
# Copied from https://github.com/mozilla-conduit/lando-api/blob/4b583f9d773dfc8c3e8c39e3d3b7385568d744df/landoapi/commit_message.py

SPECIFIER = r"(?:r|a|sr|rs|ui-r)[=?]"
R_SPECIFIER = r"\br[=?]"
R_SPECIFIER_RE = re.compile(R_SPECIFIER)

LIST = r"[;,\/\\]\s*"

# Note that we only allows a subset of legal IRC-nick characters.
# Specifically, we do not allow [ \ ] ^ ` { | }
IRC_NICK = r"[a-zA-Z0-9\-\_]+"

# fmt: off
REVIEWERS_RE = re.compile(  # noqa: E131
    r"([\s\(\.\[;,])"                   # before "r" delimiter
    + r"(" + SPECIFIER + r")"           # flag
    + r"("                              # capture all reviewers
        + r"#?"                         # Optional "#" group reviewer prefix
        + IRC_NICK                      # reviewer
        + r"!?"                         # Optional "!" blocking indicator
        + r"(?:"                        # additional reviewers
            + LIST                      # delimiter
            + r"(?![a-z0-9\.\-]+[=?])"  # don"t extend match into next flag
            + r"#?"                     # Optional "#" group reviewer prefix
            + IRC_NICK                  # reviewer
            + r"!?"                     # Optional "!" blocking indicator
        + r")*"
    + r")?"
)
# fmt: on


def replace_reviewers(commit_description, reviewers):
    if not reviewers:
        reviewers_str = ""
    else:
        reviewers_str = "r=" + ",".join(reviewers)

    if commit_description == "":
        return reviewers_str

    commit_description = commit_description.splitlines()
    commit_summary = commit_description.pop(0)
    commit_description = "\n".join(commit_description)

    if not R_SPECIFIER_RE.search(commit_summary):
        commit_summary += " " + reviewers_str
    else:
        # replace the first r? with the reviewer list, and all subsequent
        # occurrences with a marker to mark the blocks we need to remove
        # later
        d = {"first": True}

        def replace_first_reviewer(matchobj):
            if R_SPECIFIER_RE.match(matchobj.group(2)):
                if d["first"]:
                    d["first"] = False
                    return matchobj.group(1) + reviewers_str
                else:
                    return "\0"
            else:
                return matchobj.group(0)

        commit_summary = re.sub(REVIEWERS_RE, replace_first_reviewer, commit_summary)

        # remove marker values as well as leading separators.  this allows us
        # to remove runs of multiple reviewers and retain the trailing
        # separator.
        commit_summary = re.sub(LIST + "\0", "", commit_summary)
        commit_summary = re.sub("\0", "", commit_summary)

    if commit_description == "":
        return commit_summary.strip()
    else:
        return commit_summary.strip() + "\n" + commit_description


# ------------------------------------------------------------------------------


class CommitClassifier(object):
    def __init__(self, cache_root, git_repo_dir, method_defect_predictor_dir):
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

        self.method_defect_predictor_dir = method_defect_predictor_dir
        self.clone_git_repo(
            "https://github.com/lucapascarella/MethodDefectPredictor",
            method_defect_predictor_dir,
            "fa5269b959d8ddf7e97d1e92523bb64c17f9bbcd",
        )
        self.git_repo_dir = git_repo_dir
        self.clone_git_repo("https://github.com/mozilla/gecko-dev", git_repo_dir)

    def clone_git_repo(self, repo_url, repo_dir, rev="master"):
        logger.info(f"Cloning {repo_url}...")

        if not os.path.exists(repo_dir):
            retry(
                lambda: subprocess.run(["git", "clone", repo_url, repo_dir], check=True)
            )

        retry(
            lambda: subprocess.run(
                ["git", "pull", repo_url, "master"],
                cwd=repo_dir,
                capture_output=True,
                check=True,
            )
        )

        retry(
            lambda: subprocess.run(
                ["git", "checkout", rev], cwd=repo_dir, capture_output=True, check=True
            )
        )

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
            diff["phid"]: phabricator_api.load_revision(
                rev_phid=diff["revisionPHID"], attachments={"reviewers": True}
            )
            for diff in diffs
        }

        # Update repo to base revision
        hg_base = needed_stack[0].base_revision
        if not has_revision(hg_base):
            logger.warning("Missing base revision {} from Phabricator".format(hg_base))
            hg_base = "tip"

        if hg_base:
            hg.update(rev=hg_base, clean=True)
            logger.info(f"Updated repo to {hg_base}")

            try:
                self.git_base = vcs_map.mercurial_to_git(hg_base)
                subprocess.run(
                    ["git", "checkout", "-b", "analysis_branch", self.git_base],
                    check=True,
                    cwd=self.git_repo_dir,
                )
                logger.info(f"Updated git repo to {self.git_base}")
            except Exception as e:
                logger.info(f"Updating git repo to Mercurial {hg_base} failed: {e}")

        def load_user(phid):
            if phid.startswith("PHID-USER"):
                return phabricator_api.load_user(user_phid=phid)
            elif phid.startswith("PHID-PROJ"):
                # TODO: Support group reviewers somehow.
                logger.info(f"Skipping group reviewer {phid}")
            else:
                raise Exception(f"Unsupported reviewer {phid}")

        for patch in needed_stack:
            revision = revisions[patch.phid]

            message = "{}\n\n{}".format(
                revision["fields"]["title"], revision["fields"]["summary"]
            )

            author_name = None
            author_email = None

            if patch.commits:
                author_name = patch.commits[0]["author"]["name"]
                author_email = patch.commits[0]["author"]["email"]

            if author_name is None:
                author = load_user(revision["fields"]["authorPHID"])
                author_name = author["fields"]["realName"]
                # XXX: Figure out a way to know the email address of the author.
                author_email = author["fields"]["username"]

            reviewers = list(
                filter(
                    None,
                    (
                        load_user(reviewer["reviewerPHID"])
                        for reviewer in revision["attachments"]["reviewers"][
                            "reviewers"
                        ]
                    ),
                )
            )
            reviewers = set(reviewer["fields"]["username"] for reviewer in reviewers)

            if len(reviewers):
                message = replace_reviewers(message, reviewers)

            logger.info(
                f"Applying {patch.phid} from revision {revision['id']}: {message}"
            )

            hg.import_(
                patches=io.BytesIO(patch.patch.encode("utf-8")),
                message=message.encode("utf-8"),
                user=f"{author_name} <{author_email}>".encode("utf-8"),
            )

            with tempfile.TemporaryDirectory() as tmpdirname:
                temp_file = os.path.join(tmpdirname, "temp.patch")
                with open(temp_file, "w") as f:
                    f.write(patch.patch)

                subprocess.run(
                    ["git", "apply", "--3way", temp_file],
                    check=True,
                    cwd=self.git_repo_dir,
                )
                subprocess.run(
                    [
                        "git",
                        "-c",
                        f"user.name={author_name}",
                        "-c",
                        f"user.email={author_email}",
                        "commit",
                        "-am",
                        message,
                    ],
                    check=True,
                    cwd=self.git_repo_dir,
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

        # We use "clean" (or "dirty") commits as the background dataset for feature importance.
        # This way, we can see the features which are most important in differentiating
        # the current commit from the "clean" (or "dirty") commits.

        probs, importance = self.model.classify(
            commits[-1],
            probabilities=True,
            importances=True,
            background_dataset=lambda v: self.X[self.y != v],
            importance_cutoff=0.05,
        )

        pred_class = self.model.le.inverse_transform([probs[0].argmax()])[0]

        features = []
        for i, (val, feature_index, is_positive) in enumerate(
            importance["importances"]["classes"][pred_class][0]
        ):
            value = importance["importances"]["values"][0, int(feature_index)]

            X = self.X[:, int(feature_index)]
            y = self.y[X != 0]
            X = X[X != 0]
            spearman = spearmanr(X, y)

            buggy_X = X[y == 1]
            clean_X = X[y == 0]
            median = np.median(X)
            median_clean = np.median(clean_X)
            median_buggy = np.median(buggy_X)

            perc_buggy_values_higher_than_median = (
                buggy_X >= median
            ).sum() / buggy_X.shape[0]
            perc_buggy_values_lower_than_median = (
                buggy_X < median
            ).sum() / buggy_X.shape[0]
            perc_clean_values_higher_than_median = (
                clean_X > median
            ).sum() / clean_X.shape[0]
            perc_clean_values_lower_than_median = (
                clean_X <= median
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
                    "shap": float(f'{"+" if (is_positive) else "-"}{val}'),
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

        # Group together features that are very similar to each other, so we can simplify the explanation
        # to users.
        attributes = ["Total", "Maximum", "Minimum", "Average"]
        already_added = set()
        feature_groups = []
        for i1, f1 in enumerate(features):
            if i1 in already_added:
                continue

            feature_groups.append([f1])

            for j, f2 in enumerate(features[i1 + 1 :]):
                i2 = j + i1 + 1

                f1_name = f1["name"]
                for attribute in attributes:
                    if f1_name.startswith(attribute):
                        f1_name = f1_name[len(attribute) + 1 :]
                        break

                f2_name = f2["name"]
                for attribute in attributes:
                    if f2_name.startswith(attribute):
                        f2_name = f2_name[len(attribute) + 1 :]
                        break

                if f1_name != f2_name:
                    continue

                already_added.add(i2)
                feature_groups[-1].append(f2)

        # Pick a representative example from each group.
        features = []
        for feature_group in feature_groups:
            shap = sum(f["shap"] for f in feature_group)

            # Only select easily explainable features from the group.
            selected = [
                f
                for f in feature_group
                if (
                    f["shap"] > 0
                    and abs(f["value"] - f["median_bug_introducing"])
                    < abs(f["value"] - f["median_clean"])
                )
                or (
                    f["shap"] < 0
                    and abs(f["value"] - f["median_clean"])
                    < abs(f["value"] - f["median_bug_introducing"])
                )
            ]

            # If there are no easily explainable features in the group, select all features of the group.
            if len(selected) == 0:
                selected = feature_group

            def feature_sort_key(f):
                if f["shap"] > 0 and f["spearman"][0] > 0:
                    return f["perc_buggy_values_higher_than_median"]
                elif f["shap"] > 0 and f["spearman"][0] < 0:
                    return f["perc_buggy_values_lower_than_median"]
                elif f["shap"] < 0 and f["spearman"][0] > 0:
                    return f["perc_clean_values_lower_than_median"]
                elif f["shap"] < 0 and f["spearman"][0] < 0:
                    return f["perc_clean_values_higher_than_median"]

            feature = max(selected, key=feature_sort_key)
            feature["shap"] = shap

            for attribute in attributes:
                if feature["name"].startswith(attribute):
                    feature["name"] = feature["name"][len(attribute) + 1 :].capitalize()
                    break

            features.append(feature)

        with open("probs.json", "w") as f:
            json.dump(probs[0].tolist(), f)

        with open("importances.json", "w") as f:
            json.dump(features, f)

        # Get commit hash from 4 months before the analysis time.
        # The method-level analyzer needs 4 months of history.
        four_months_ago = datetime.utcnow() - relativedelta(months=4)
        p = subprocess.run(
            [
                "git",
                "rev-list",
                "-n",
                "1",
                "--until={}".format(four_months_ago.strftime("%Y-%m-%d")),
                "HEAD",
            ],
            check=True,
            capture_output=True,
            cwd=self.git_repo_dir,
        )

        stop_hash = p.stdout.decode().strip()

        # Run the method-level analyzer.
        subprocess.run(
            [
                "python3",
                "tester.py",
                "--repo",
                self.git_repo_dir,
                "--start",
                "HEAD",
                "--stop",
                stop_hash,
                "--output",
                os.path.abspath("method_level.csv"),
            ],
            check=True,
            cwd=self.method_defect_predictor_dir,
        )

        method_level_results = []
        try:
            with open("method_level.csv", "r") as f:
                reader = csv.DictReader(f)
                for item in reader:
                    method_level_results.append(item)
        except FileNotFoundError:
            # No methods were classified.
            pass

        with open("method_level.json", "w") as f:
            json.dump(method_level_results, f)


def main():
    description = "Classify a commit"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("cache_root", help="Cache for repository clones.")
    parser.add_argument("diff_id", help="diff ID to analyze.", type=int)
    parser.add_argument(
        "git_repo_dir", help="Path where the git repository will be cloned."
    )
    parser.add_argument(
        "method_defect_predictor_dir",
        help="Path where the git repository will be cloned.",
    )

    args = parser.parse_args()

    classifier = CommitClassifier(
        args.cache_root, args.git_repo_dir, args.method_defect_predictor_dir
    )
    classifier.classify(args.diff_id)


if __name__ == "__main__":
    main()
