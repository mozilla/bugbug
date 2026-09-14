# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Run a test selection model on a try or autoland push and explain the results.

The script downloads the requested test selection model and the databases it
needs at runtime, mines the commits of the given push from a local Mercurial
clone, builds the model input for every schedulable task/group, logs the
features, applies the model and logs the feature importances (SHAP values) of
the inference.

Examples:
    python -m scripts.test_select_classifier testlabelselect /path/to/autoland \
        --branch try --rev 0123456789ab

    python -m scripts.test_select_classifier testgroupselect \
        --commits-json scripts/test_select_classifier_example.json
"""

import argparse
import json
import math
import os
from datetime import datetime, timezone
from logging import INFO, basicConfig, getLogger
from typing import Any

import numpy as np
import requests
import shap
from tabulate import tabulate

from bugbug import commit_features, db, repository, test_scheduling
from bugbug.model import get_transformer_pipeline
from bugbug.models import get_model_class
from bugbug.models.testselect import TestSelectModel
from bugbug.utils import download_model, get_hgmo_stack, to_array

basicConfig(level=INFO)
logger = getLogger(__name__)

MODEL_NAMES = ("testlabelselect", "testgroupselect")

BRANCHES = {
    "try": "try",
    "autoland": "integration/autoland",
    "integration/autoland": "integration/autoland",
}

# Example of a JSON file to pass to --commits-json, to run the model on synthetic
# data instead of a real push. The file contains a list of commits (or a single
# commit object). Only "files" is required: "types", "directories" and the file
# counts are derived from it, and every other field a mined commit has (desc,
# components, reviewers, added/deleted lines, file sizes, metrics, ...) falls
# back to a neutral default unless specified. The same example is available in
# scripts/test_select_classifier_example.json.
EXAMPLE_COMMITS_JSON = """\
[
  {
    "desc": "Bug 1234567 - Fix document loading in nsDocument. r=smaug",
    "files": [
      "dom/base/nsDocument.cpp",
      "dom/base/nsDocument.h",
      "dom/base/test/test_bug1234567.html",
      "dom/base/test/mochitest.toml"
    ],
    "components": ["Core::DOM: Core & HTML"],
    "reviewers": ["smaug"],
    "source_code_added": 42,
    "source_code_deleted": 10,
    "test_added": 25
  },
  {
    "desc": "Bug 1234567 - Update browser toolbar for the new document API. r=mossop",
    "files": [
      "browser/components/urlbar/UrlbarInput.sys.mjs",
      "browser/components/urlbar/tests/browser/browser_urlbar_input.js"
    ],
    "components": ["Firefox::Address Bar"],
    "reviewers": ["mossop"]
  }
]
"""

# Databases (support files of the test scheduling history DBs) that the models
# need at inference time, by granularity. The failing together DBs are not
# needed to apply the model itself, only to reduce the selected configurations.
RUNTIME_DBS = {
    "label": (
        (
            test_scheduling.TEST_LABEL_SCHEDULING_DB,
            test_scheduling.PAST_FAILURES_LABEL_DB,
        ),
    ),
    "group": (
        (
            test_scheduling.TEST_GROUP_SCHEDULING_DB,
            test_scheduling.PAST_FAILURES_GROUP_DB,
        ),
        (
            test_scheduling.TEST_GROUP_SCHEDULING_DB,
            test_scheduling.TOUCHED_TOGETHER_DB,
        ),
    ),
}


def load_model(model_name: str) -> TestSelectModel:
    model_file_name = f"{model_name}model"

    if not os.path.exists(model_file_name):
        logger.info("%s does not exist. Downloading the model...", model_file_name)
    else:
        logger.info("Checking for an updated %s...", model_file_name)

    try:
        download_model(model_name)
    except requests.HTTPError:
        logger.error(
            "A pre-trained model is not available, you will need to train it yourself using the trainer script"
        )
        raise SystemExit(1)

    model = get_model_class(model_name).load(model_file_name)
    assert isinstance(model, TestSelectModel)
    return model


def download_runtime_dbs(granularity: str) -> None:
    for scheduling_db, support_file in RUNTIME_DBS[granularity]:
        logger.info("Downloading %s...", support_file)
        assert db.download_support_file(scheduling_db, support_file), (
            f"{support_file} is not available for download"
        )


def get_push_commits(
    repo_dir: str, branch: str, rev: str
) -> tuple[repository.CommitDict, ...]:
    if not os.path.exists(repo_dir):
        logger.info("Cloning autoland in %s...", repo_dir)
        repository.clone(
            repo_dir, "https://hg.mozilla.org/integration/autoland", update=True
        )

    logger.info("Pulling %s from %s into %s...", rev, branch, repo_dir)
    repository.pull(repo_dir, branch, rev, update=False)

    logger.info("Loading the stack of commits of the push using automationrelevance...")
    revs = get_hgmo_stack(branch, rev)
    logger.info(
        "%d commits in the push: %s",
        len(revs),
        ", ".join(r.decode("ascii")[:12] for r in revs),
    )

    # On "try", consider commits from other branches too (see https://bugzilla.mozilla.org/show_bug.cgi?id=1790493).
    # On other repos, only consider "default" commits.
    repo_branch = None if branch == "try" else "default"

    commits = repository.download_commits(
        repo_dir,
        revs=revs,
        branch=repo_branch,
        save=False,
        include_no_bug=True,
    )
    assert len(commits) > 0, "There are no commits to analyze"

    return commits


# Fields of a mined commit that merge_commits needs, with the default used when a
# commit in the JSON file doesn't specify them.
SYNTHETIC_COMMIT_DEFAULTS: dict[str, Any] = {
    "desc": "",
    "components": [],
    "reviewers": [],
    "total_source_code_file_size": 0,
    "maximum_source_code_file_size": 0,
    "minimum_source_code_file_size": 0,
    "total_other_file_size": 0,
    "maximum_other_file_size": 0,
    "minimum_other_file_size": 0,
    "total_test_file_size": 0,
    "maximum_test_file_size": 0,
    "minimum_test_file_size": 0,
    "source_code_added": 0,
    "other_added": 0,
    "test_added": 0,
    "source_code_deleted": 0,
    "other_deleted": 0,
    "test_deleted": 0,
}


def load_commits_from_json(path: str) -> tuple[repository.CommitDict, ...]:
    """Build commits from a JSON file, to run the model on synthetic data.

    The file contains a list of commits (or a single commit object). The only
    required field is "files"; "types", "directories" and the file counts are
    derived from it when missing, and every other field mined from a real
    commit falls back to a neutral default. Any field can be overridden.
    """
    with open(path, "r") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = [data]

    assert isinstance(data, list) and len(data) > 0, (
        f"{path} must contain a non-empty list of commits"
    )

    commits = []
    for i, item in enumerate(data):
        assert "files" in item and len(item["files"]) > 0, (
            f"Commit {i} in {path} must specify a non-empty 'files' list"
        )

        files = list(item["files"])
        test_files = [f for f in files if repository.is_test(f)]
        source_code_files = [
            f
            for f in files
            if f not in test_files
            and repository.get_type(f) in repository.SOURCE_CODE_TYPES_TO_EXT
        ]

        commit: dict[str, Any] = {
            "node": f"{i:040x}",
            "pushdate": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "files": files,
            "types": sorted(set(repository.get_type(f) for f in files)),
            "directories": repository.get_directories(files),
            "source_code_files_modified_num": len(source_code_files),
            "test_files_modified_num": len(test_files),
            "other_files_modified_num": len(files)
            - len(source_code_files)
            - len(test_files),
            "metrics": repository.get_metrics_dict(),
        }
        commit.update(SYNTHETIC_COMMIT_DEFAULTS)
        commit.update(item)

        commits.append(repository.CommitDict(commit))

    logger.info("Loaded %d synthetic commits from %s", len(commits), path)

    return tuple(commits)


def build_model_input(
    model: TestSelectModel, commits: tuple[repository.CommitDict, ...]
) -> list[dict[str, Any]]:
    """Build one input item per schedulable runnable, as TestSelectModel.select_tests does."""
    commit_data = commit_features.merge_commits(commits)

    logger.info("Merged commit data:")
    for key in (
        "nodes",
        "pushdate",
        "types",
        "components",
        "directories",
        "files",
    ):
        logger.info("  %s: %s", key, commit_data[key])

    # Not readonly, as the group-level past failures DB might need to record
    # INI to TOML manifest renames on read (see PastFailures.get).
    past_failures_data = test_scheduling.PastFailures(model.granularity, False)
    push_num = past_failures_data.push_num + 1
    logger.info(
        "Using push number %d (the past failures DB contains data up to push %d)",
        push_num,
        past_failures_data.push_num,
    )

    commit_tests = []
    for data in test_scheduling.generate_data(
        model.granularity,
        past_failures_data,
        commit_data,
        push_num,
        past_failures_data.all_runnables,
        tuple(),
        tuple(),
    ):
        commit_test = commit_data.copy()
        commit_test["test_job"] = data
        commit_tests.append(commit_test)

    logger.info("%d runnables to classify", len(commit_tests))

    return commit_tests


def format_features(features: dict[str, Any]) -> str:
    return ", ".join(f"{name}={value}" for name, value in sorted(features.items()))


def normalize_shap_values(shap_values) -> list[np.ndarray]:
    """Normalize the SHAP output to a list with one matrix per class."""
    if isinstance(shap_values, list):
        return shap_values

    # Multi-class case: (n_samples, n_features, n_classes).
    if shap_values.ndim == 3:
        return [shap_values[:, :, i] for i in range(shap_values.shape[2])]

    # In the binary case, shap returns a single matrix for the positive class.
    return [-shap_values, shap_values]


def classify(
    model: TestSelectModel,
    commit_tests: list[dict[str, Any]],
    confidence_threshold: float,
    limit: int | None,
    top_features: int,
    importance_cutoff: float,
    runnables: list[str] | None = None,
) -> None:
    logger.info("Extracting features...")
    X = model.extraction_pipeline.transform(lambda: commit_tests)

    logger.info("Applying the model...")
    probs = model.clf.predict_proba(X)

    names = [commit_test["test_job"]["name"] for commit_test in commit_tests]
    order = np.argsort(-probs[:, 1], kind="stable")

    selected = [i for i in order if probs[i, 1] >= confidence_threshold]
    logger.info(
        "%d out of %d runnables selected with confidence >= %s",
        len(selected),
        len(names),
        confidence_threshold,
    )

    if runnables:
        # Explain the requested runnables, whether they were selected or not.
        index_by_name = {name: i for i, name in enumerate(names)}
        to_log = []
        for runnable in runnables:
            if runnable in index_by_name:
                to_log.append(index_by_name[runnable])
            else:
                logger.error(
                    "Runnable %s is not among the %d schedulable runnables",
                    runnable,
                    len(names),
                )
        assert to_log, "None of the requested runnables was found"
    elif selected:
        to_log = selected
    else:
        fallback_num = limit if limit is not None else 10
        logger.warning(
            "No runnable was selected, showing the %d runnables with the highest confidence",
            fallback_num,
        )
        to_log = list(order[:fallback_num])

    if limit is not None:
        to_log = to_log[:limit]

    logger.info(
        "%s:\n%s",
        "Requested runnables" if runnables else "Selected runnables",
        tabulate(
            [(names[i], f"{math.floor(probs[i, 1] * 100) / 100:.2f}") for i in to_log],
            headers=["Runnable", "Confidence"],
            tablefmt="grid",
        ),
    )

    logger.info("Computing feature importances for %d runnables...", len(to_log))
    feature_names = model.get_human_readable_feature_names()
    explainer = shap.TreeExplainer(model.clf.named_steps["estimator"])
    _X = to_array(get_transformer_pipeline(model.clf).transform(X.iloc[to_log]))
    shap_values = normalize_shap_values(explainer.shap_values(_X))
    positive_shap_values = shap_values[1]

    for row, i in enumerate(to_log):
        logger.info(
            "%s - confidence %.4f (probabilities %s)", names[i], probs[i, 1], probs[i]
        )
        logger.info("  Features: %s", format_features(X.iloc[i]["data"]))

        contributions = positive_shap_values[row]
        top_indexes = np.argsort(-np.abs(contributions), kind="stable")[:top_features]
        table = [
            (
                feature_names[j],
                _X[row, j],
                f"{'+' if contributions[j] >= 0 else '-'}{abs(contributions[j]):.4f}",
            )
            for j in top_indexes
            if contributions[j] != 0
        ]
        logger.info(
            "  Top %d features by SHAP value:\n%s",
            len(table),
            tabulate(table, headers=["Feature", "Value", "SHAP"], tablefmt="grid"),
        )

    logger.info(
        "Feature importances aggregated over the %d runnables above:", len(to_log)
    )
    important_features = model.get_important_features(importance_cutoff, shap_values)
    model.print_feature_importances(important_features)


def main() -> None:
    description = "Apply a test selection model to a try or autoland push, logging features and feature importances"
    epilog = (
        "Example JSON file for --commits-json (only 'files' is required per commit, "
        "the other fields are optional):\n\n" + EXAMPLE_COMMITS_JSON
    )
    parser = argparse.ArgumentParser(
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "model", help="Which model to use for evaluation", choices=MODEL_NAMES
    )
    parser.add_argument(
        "repo_dir",
        nargs="?",
        help="Path to a Gecko repository. If no repository exists, autoland will be cloned to this location. Not needed with --commits-json.",
    )
    parser.add_argument(
        "--branch",
        help="Branch of the push to analyze",
        choices=sorted(BRANCHES.keys()),
        default="autoland",
    )
    parser.add_argument("--rev", help="Revision (tip of the push) to analyze")
    parser.add_argument(
        "--commits-json",
        help="Path to a JSON file describing synthetic commits to analyze instead of a push (see the example at the end of this help)",
    )
    parser.add_argument(
        "--confidence-threshold",
        help="Minimum confidence for a runnable to be selected",
        type=float,
        default=0.5,
    )
    parser.add_argument(
        "--runnable",
        action="append",
        dest="runnables",
        metavar="NAME",
        help="Show features and importances for this runnable (task label or manifest), whether selected or not. Can be repeated.",
    )
    parser.add_argument(
        "--limit",
        help="Maximum number of selected runnables to log features and importances for (default: all)",
        type=int,
    )
    parser.add_argument(
        "--top-features",
        help="Number of features to log for each runnable, ordered by absolute SHAP value",
        type=int,
        default=10,
    )
    parser.add_argument(
        "--importance-cutoff",
        help="Cutoff for the aggregated feature importances, as a fraction of the most important feature",
        type=float,
        default=0.15,
    )

    args = parser.parse_args()

    if args.commits_json is None and (args.repo_dir is None or args.rev is None):
        parser.error("either --commits-json or both repo_dir and --rev are required")

    model = load_model(args.model)
    download_runtime_dbs(model.granularity)

    if args.commits_json is not None:
        commits = load_commits_from_json(args.commits_json)
    else:
        commits = get_push_commits(args.repo_dir, BRANCHES[args.branch], args.rev)

    commit_tests = build_model_input(model, commits)

    classify(
        model,
        commit_tests,
        args.confidence_threshold,
        args.limit,
        args.top_features,
        args.importance_cutoff,
        args.runnables,
    )


if __name__ == "__main__":
    main()
