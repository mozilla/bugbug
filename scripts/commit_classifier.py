# -*- coding: utf-8 -*-

import argparse
import base64
import csv
import io
import json
import os
import pickle
import re
import subprocess
from datetime import datetime
from logging import INFO, basicConfig, getLogger
from typing import cast

import dateutil.parser
import hglib
import matplotlib
import numpy as np
import requests
import shap
import tenacity
from dateutil.relativedelta import relativedelta
from libmozdata import vcs_map
from libmozdata.phabricator import PhabricatorAPI
from scipy.stats import spearmanr

from bugbug import db, repository, test_scheduling
from bugbug.model import Model, get_transformer_pipeline
from bugbug.models.regressor import RegressorModel
from bugbug.models.testfailure import TestFailureModel
from bugbug.utils import (
    download_check_etag,
    download_model,
    get_secret,
    to_array,
    zstd_decompress,
)

basicConfig(level=INFO)
logger = getLogger(__name__)

URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.train_{model_name}.latest/artifacts/public/{file_name}"
PAST_BUGS_BY_FUNCTION_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.past_bugs_by_unit.latest/artifacts/public/past_fixed_bugs_by_function.json.zst"
PHAB_PROD = "prod"
PHAB_DEV = "dev"


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
REVIEWERS_RE = re.compile(
    r"([\s\(\.\[;,])"                   # before "r" delimiter
    + r"(" + SPECIFIER + r")"           # flag
    + r"("                              # capture all reviewers
        + r"#?"                         # Optional "#" group reviewer prefix  # noqa: E131
        + IRC_NICK                      # reviewer
        + r"!?"                         # Optional "!" blocking indicator
        + r"(?:"                        # additional reviewers
            + LIST                      # delimiter  # noqa: E131
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
    def __init__(
        self,
        model_name: str,
        repo_dir: str,
        git_repo_dir: str,
        method_defect_predictor_dir: str,
        use_single_process: bool,
        skip_feature_importance: bool,
        phabricator_deployment: str | None = None,
        diff_id: int | None = None,
    ):
        self.model_name = model_name
        self.repo_dir = repo_dir

        self.model = Model.load(download_model(model_name))
        assert self.model is not None

        self.git_repo_dir = git_repo_dir
        if git_repo_dir:
            self.clone_git_repo(
                "hg::https://hg.mozilla.org/mozilla-unified", git_repo_dir
            )

        self.revision = None
        if diff_id is not None:
            assert phabricator_deployment is not None
            with hglib.open(self.repo_dir) as hg:
                self.apply_phab(hg, phabricator_deployment, diff_id)

                self.revision = hg.log(revrange="not public()")[0].node.decode("utf-8")

        self.method_defect_predictor_dir = method_defect_predictor_dir
        if method_defect_predictor_dir:
            self.clone_git_repo(
                "https://github.com/lucapascarella/MethodDefectPredictor",
                method_defect_predictor_dir,
                "8cc47f47ffb686a29324435a0151b5fabd37f865",
            )

        self.use_single_process = use_single_process
        self.skip_feature_importance = skip_feature_importance

        if model_name == "regressor":
            self.use_test_history = False

            model_data_X_path = f"{model_name}model_data_X"
            updated = download_check_etag(
                URL.format(model_name=model_name, file_name=f"{model_data_X_path}.zst")
            )
            if updated:
                zstd_decompress(model_data_X_path)
            assert os.path.exists(model_data_X_path), "Decompressed X dataset exists"

            model_data_y_path = f"{model_name}model_data_y"
            updated = download_check_etag(
                URL.format(model_name=model_name, file_name=f"{model_data_y_path}.zst")
            )
            if updated:
                zstd_decompress(model_data_y_path)
            assert os.path.exists(model_data_y_path), "Decompressed y dataset exists"

            with open(model_data_X_path, "rb") as fb:
                self.X = to_array(pickle.load(fb))

            with open(model_data_y_path, "rb") as fb:
                self.y = to_array(pickle.load(fb))

            past_bugs_by_function_path = "data/past_fixed_bugs_by_function.json"
            download_check_etag(
                PAST_BUGS_BY_FUNCTION_URL, path=f"{past_bugs_by_function_path}.zst"
            )
            zstd_decompress(past_bugs_by_function_path)
            assert os.path.exists(past_bugs_by_function_path)
            with open(past_bugs_by_function_path, "r") as f:
                self.past_bugs_by_function = json.load(f)

        if model_name == "testlabelselect":
            self.use_test_history = True
            assert db.download_support_file(
                test_scheduling.TEST_LABEL_SCHEDULING_DB,
                test_scheduling.PAST_FAILURES_LABEL_DB,
            )
            self.past_failures_data = test_scheduling.PastFailures("label", True)

            self.testfailure_model = cast(
                TestFailureModel, TestFailureModel.load(download_model("testfailure"))
            )
            assert self.testfailure_model is not None

    def clone_git_repo(self, repo_url, repo_dir, rev="origin/branches/default/tip"):
        logger.info("Cloning %s...", repo_url)

        if not os.path.exists(repo_dir):
            tenacity.retry(
                wait=tenacity.wait_exponential(multiplier=2, min=2),
                stop=tenacity.stop_after_attempt(7),
            )(
                lambda: subprocess.run(
                    ["git", "clone", "--quiet", repo_url, repo_dir], check=True
                )
            )()

        tenacity.retry(
            wait=tenacity.wait_exponential(multiplier=2, min=2),
            stop=tenacity.stop_after_attempt(7),
        )(
            lambda: subprocess.run(
                ["git", "fetch"],
                cwd=repo_dir,
                capture_output=True,
                check=True,
            )
        )()

        subprocess.run(
            ["git", "checkout", rev], cwd=repo_dir, capture_output=True, check=True
        )

    def update_commit_db(self):
        repository.clone(
            self.repo_dir, "https://hg.mozilla.org/mozilla-unified", update=True
        )

        assert db.download(repository.COMMITS_DB, support_files_too=True)

        for commit in repository.get_commits():
            pass

        repository.download_commits(
            self.repo_dir,
            rev_start="children({})".format(commit["node"]),
            use_single_process=self.use_single_process,
        )

    def has_revision(self, hg, revision):
        if not revision:
            return False
        try:
            hg.identify(revision)
            return True
        except hglib.error.CommandError:
            return False

    def apply_phab(self, hg, phabricator_deployment, diff_id):
        if phabricator_deployment == PHAB_PROD:
            api_key = get_secret("PHABRICATOR_TOKEN")
            url = get_secret("PHABRICATOR_URL")
        else:
            api_key = get_secret("PHABRICATOR_DEV_TOKEN")
            url = get_secret("PHABRICATOR_DEV_URL")

        phabricator_api = PhabricatorAPI(api_key=api_key, url=url)

        # Get the stack of patches
        stack = phabricator_api.load_patches_stack(diff_id)
        assert len(stack) > 0, "No patches to apply"

        # Find the first unknown base revision
        needed_stack = []
        revisions = {}
        for patch in reversed(stack):
            needed_stack.insert(0, patch)

            # Stop as soon as a base revision is available
            if self.has_revision(hg, patch.base_revision):
                logger.info(
                    "Stopping at diff %s and revision %s", patch.id, patch.base_revision
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
        if not self.has_revision(hg, hg_base):
            logger.warning("Missing base revision {} from Phabricator".format(hg_base))
            hg_base = "tip"

        if hg_base:
            hg.update(rev=hg_base, clean=True)
            logger.info("Updated repo to %s", hg_base)

            if self.git_repo_dir and hg_base != "tip":
                try:
                    self.git_base = tuple(
                        vcs_map.mercurial_to_git(self.git_repo_dir, [hg_base])
                    )[0]
                    subprocess.run(
                        ["git", "checkout", "-b", "analysis_branch", self.git_base],
                        check=True,
                        cwd=self.git_repo_dir,
                    )
                    logger.info("Updated git repo to %s", self.git_base)
                except Exception as e:
                    logger.info(
                        "Updating git repo to Mercurial %s failed: %s", hg_base, e
                    )

        def load_user(phid):
            if phid.startswith("PHID-USER"):
                return phabricator_api.load_user(user_phid=phid)
            elif phid.startswith("PHID-PROJ"):
                # TODO: Support group reviewers somehow.
                logger.info("Skipping group reviewer %s", phid)
            else:
                raise ValueError(f"Unsupported reviewer {phid}")

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
                "Applying %s from revision %s: %s", patch.phid, revision["id"], message
            )

            hg.import_(
                patches=io.BytesIO(patch.patch.encode("utf-8")),
                message=message.encode("utf-8"),
                user=f"{author_name} <{author_email}>".encode("utf-8"),
            )

        latest_rev = repository.get_revs(hg, -1)[0]

        if self.git_repo_dir:
            subprocess.run(
                ["git", "cinnabar", "fetch", f"hg::{self.repo_dir}", latest_rev],
                check=True,
                cwd=self.git_repo_dir,
            )

    def generate_feature_importance_data(self, probs, importance):
        _X = get_transformer_pipeline(self.clf).transform(self.X)
        X_shap_values = shap.TreeExplainer(
            self.clf.named_steps["estimator"]
        ).shap_values(_X)

        pred_class = self.model.le.inverse_transform([probs[0].argmax()])[0]

        features = []
        for i, (val, feature_index, is_positive) in enumerate(
            importance["importances"]["classes"][pred_class][0]
        ):
            name = importance["feature_legend"][str(i + 1)]
            value = importance["importances"]["values"][0, int(feature_index)]

            shap.summary_plot(
                X_shap_values[:, int(feature_index)].reshape(_X.shape[0], 1),
                _X[:, int(feature_index)].reshape(_X.shape[0], 1),
                feature_names=[""],
                plot_type="layered_violin",
                show=False,
            )
            matplotlib.pyplot.xlabel("Impact on model output")
            img = io.BytesIO()
            matplotlib.pyplot.savefig(img, bbox_inches="tight")
            matplotlib.pyplot.clf()
            img.seek(0)
            base64_img = base64.b64encode(img.read()).decode("ascii")

            X = _X[:, int(feature_index)]
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

            logger.info("Feature: {}".format(name))
            logger.info("Shap value: {}{}".format("+" if (is_positive) else "-", val))
            logger.info("spearman: %f", spearman)
            logger.info("value: %f", value)
            logger.info("overall mean: %f", np.mean(X))
            logger.info("overall median: %f", np.median(X))
            logger.info("mean for y == 0: %f", np.mean(clean_X))
            logger.info("mean for y == 1: %f", np.mean(buggy_X))
            logger.info("median for y == 0: %f", np.median(clean_X))
            logger.info("median for y == 1: %f", np.median(buggy_X))

            logger.info(
                "perc_buggy_values_higher_than_median: %f",
                perc_buggy_values_higher_than_median,
            )
            logger.info(
                "perc_buggy_values_lower_than_median: %f",
                perc_buggy_values_lower_than_median,
            )
            logger.info(
                "perc_clean_values_higher_than_median: %f",
                perc_clean_values_higher_than_median,
            )
            logger.info(
                "perc_clean_values_lower_than_median: %f",
                perc_clean_values_lower_than_median,
            )

            features.append(
                {
                    "index": i + 1,
                    "name": name,
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
                    "plot": base64_img,
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
            shap_sum = sum(f["shap"] for f in feature_group)

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
            feature["shap"] = shap_sum

            for attribute in attributes:
                if feature["name"].startswith(attribute):
                    feature["name"] = feature["name"][len(attribute) + 1 :].capitalize()
                    break

            features.append(feature)

        with open("importances.json", "w") as f:
            json.dump(features, f)

    def classify(
        self,
        revision: str | None = None,
        runnable_jobs_path: str | None = None,
    ) -> None:
        self.update_commit_db()

        if self.revision is not None:
            assert revision is None
            revision = self.revision

            commits = repository.download_commits(
                self.repo_dir,
                rev_start=revision,
                save=False,
                use_single_process=self.use_single_process,
            )
        else:
            assert revision is not None
            commits = tuple(
                commit
                for commit in repository.get_commits()
                if commit["node"] == revision
            )

            # The commit to analyze was not in our DB, let's mine it.
            if len(commits) == 0:
                commits = repository.download_commits(
                    self.repo_dir,
                    revs=[revision.encode("ascii")],
                    save=False,
                    use_single_process=self.use_single_process,
                )

        assert len(commits) > 0, "There are no commits to analyze"

        if not self.use_test_history:
            self.classify_regressor(commits)
        else:
            self.classify_test_select(commits, runnable_jobs_path)

    def classify_regressor(self, commits: tuple[repository.CommitDict, ...]) -> None:
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

        if not self.skip_feature_importance:
            self.generate_feature_importance_data(probs, importance)

        results = {
            "probs": probs[0].tolist(),
        }
        if self.model_name == "regressor":
            results["risk_band"] = RegressorModel.find_risk_band(probs[0][1])

        with open("results.json", "w") as f:
            json.dump(results, f)

        if self.model_name == "regressor" and self.method_defect_predictor_dir:
            self.classify_methods(commits[-1])

    def classify_test_select(self, commits, runnable_jobs_path):
        testfailure_probs = self.testfailure_model.classify(
            commits[-1], probabilities=True
        )

        logger.info("Test failure risk: %f", testfailure_probs[0][1])

        if not runnable_jobs_path:
            runnable_jobs = {}
        elif runnable_jobs_path.startswith("http"):
            r = requests.get(runnable_jobs_path)
            r.raise_for_status()
            runnable_jobs = r.json()
        else:
            with open(runnable_jobs_path, "r") as f:
                runnable_jobs = json.load(f)

        # XXX: Remove tasks which are not in runnable jobs right away, so we avoid classifying them.
        # XXX: Consider using mozilla-central built-in rules to filter some of the tasks out, e.g. SCHEDULES.

        selected_tasks = list(
            self.model.select_tests(
                commits, float(get_secret("TEST_SELECTION_CONFIDENCE_THRESHOLD"))
            ).keys()
        )

        # XXX: For now, only restrict to linux64 test tasks (as for runnable jobs above, we could remove these right away).
        selected_tasks = [
            t for t in selected_tasks if t.startswith("test-linux1804-64/")
        ]

        with open("failure_risk", "w") as f:
            f.write(
                "1"
                if testfailure_probs[0][1]
                > float(get_secret("TEST_FAILURE_CONFIDENCE_THRESHOLD"))
                else "0"
            )

        # This should be kept in sync with the test scheduling history retriever script.
        cleaned_selected_tasks = []
        if len(runnable_jobs) > 0:
            for selected_task in selected_tasks:
                if (
                    selected_task.startswith("test-linux64")
                    and selected_task not in runnable_jobs
                ):
                    selected_task = selected_task.replace(
                        "test-linux64-", "test-linux1804-64-"
                    )

                if (
                    selected_task.startswith("test-linux1804-64-")
                    and selected_task not in runnable_jobs
                ):
                    selected_task = selected_task.replace(
                        "test-linux1804-64-", "test-linux64-"
                    )

                if selected_task in runnable_jobs:
                    cleaned_selected_tasks.append(selected_task)

        # It isn't worth running the build associated to the tests, if we only run three test tasks.
        if len(cleaned_selected_tasks) < 3:
            cleaned_selected_tasks = []

        with open("selected_tasks", "w") as f:
            f.writelines(
                f"{selected_task}\n" for selected_task in cleaned_selected_tasks
            )

    def classify_methods(self, commit):
        # Get commit hash from 4 months before the analysis time.
        # The method-level analyzer needs 4 months of history.
        stop_hash = None
        four_months_ago = datetime.utcnow() - relativedelta(months=4)
        for commit in repository.get_commits():
            if dateutil.parser.parse(commit["pushdate"]) >= four_months_ago:
                stop_hash = tuple(
                    vcs_map.mercurial_to_git(self.git_repo_dir, [commit["node"]])
                )[0]
                break
        assert stop_hash is not None

        p = subprocess.run(
            [
                "git",
                "rev-list",
                "-n",
                "1",
                "HEAD",
            ],
            check=True,
            capture_output=True,
            cwd=self.git_repo_dir,
        )

        start_hash = p.stdout.decode().strip()

        # Run the method-level analyzer.
        subprocess.run(
            [
                "python3",
                "tester.py",
                "--repo",
                self.git_repo_dir,
                "--start",
                start_hash,
                "--stop",
                stop_hash,
                "--output",
                os.path.abspath("method_level.csv"),
            ],
            cwd=self.method_defect_predictor_dir,
        )

        method_level_results = []
        try:
            with open("method_level.csv", "r") as f:
                reader = csv.DictReader(f)
                for item in reader:
                    item["past_bugs"] = []
                    method_level_results.append(item)
        except FileNotFoundError:
            # No methods were classified.
            pass

        for method_level_result in method_level_results:
            method_level_result_path = method_level_result["file_name"]
            if method_level_result_path not in self.past_bugs_by_function:
                continue

            for path, functions in commit["functions"].items():
                if method_level_result_path != path:
                    continue

                for function in functions:
                    function_name = function["name"]
                    if function_name not in self.past_bugs_by_function[path]:
                        continue

                    if method_level_result["method_name"].endswith(function_name):
                        method_level_result["past_bugs"] = [
                            "Bug {} - {}".format(bug["id"], bug["summary"])
                            for bug in self.past_bugs_by_function[path][function_name][
                                -3:
                            ]
                        ]

        with open("method_level.json", "w") as f:
            json.dump(method_level_results, f)


def main() -> None:
    description = "Classify a commit"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", help="Which model to use for evaluation")
    parser.add_argument(
        "repo_dir",
        help="Path to a Gecko repository. If no repository exists, it will be cloned to this location.",
    )
    parser.add_argument(
        "--phabricator-deployment",
        help="Which Phabricator deployment to hit.",
        type=str,
        choices=[PHAB_PROD, PHAB_DEV],
    )
    parser.add_argument("--diff-id", help="diff ID to analyze.", type=int)
    parser.add_argument("--revision", help="revision to analyze.", type=str)
    parser.add_argument(
        "--runnable-jobs",
        help="Path or URL to a file containing runnable jobs.",
        type=str,
    )
    parser.add_argument(
        "--git_repo_dir", help="Path where the git repository will be cloned."
    )
    parser.add_argument(
        "--method_defect_predictor_dir",
        help="Path where the git repository will be cloned.",
    )
    parser.add_argument(
        "--use-single-process",
        action="store_true",
        help="Whether to use a single process.",
    )
    parser.add_argument(
        "--skip-feature-importance",
        action="store_true",
        help="Whether to skip feature importance calculation.",
    )

    args = parser.parse_args()

    if args.revision is not None:
        assert args.phabricator_deployment is None
        assert args.diff_id is None

    if args.diff_id is not None:
        assert args.phabricator_deployment is not None
        assert args.revision is None

    classifier = CommitClassifier(
        args.model,
        args.repo_dir,
        args.git_repo_dir,
        args.method_defect_predictor_dir,
        args.use_single_process,
        args.skip_feature_importance,
        args.phabricator_deployment,
        args.diff_id,
    )
    classifier.classify(args.revision, args.runnable_jobs)


if __name__ == "__main__":
    main()
