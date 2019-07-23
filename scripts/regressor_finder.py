# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import csv
import itertools
import os
import subprocess
from collections import defaultdict
from datetime import datetime
from logging import INFO, basicConfig, getLogger

import dateutil.parser
import hglib
import zstandard
from dateutil.relativedelta import relativedelta
from libmozdata import vcs_map
from pydriller import GitRepository
from tqdm import tqdm

from bugbug import bugzilla, db, repository
from bugbug.models.defect_enhancement_task import DefectEnhancementTaskModel
from bugbug.models.regression import RegressionModel
from bugbug.utils import download_check_etag, retry

basicConfig(level=INFO)
logger = getLogger(__name__)


MAX_MODIFICATION_NUMBER = 50
# TODO: Set to 2 years and 6 months. If it takes too long, make the task work incrementally like microannotate-generate.
RELATIVE_START_DATE = relativedelta(days=49)
# Only needed because mercurial<->git mapping could be behind.
RELATIVE_END_DATE = relativedelta(days=3)

BUG_FIXING_COMMITS_DB = "data/bug_fixing_commits.json"
db.register(
    BUG_FIXING_COMMITS_DB,
    "https://index.taskcluster.net/v1/task/project.relman.bugbug_annotate.regressor_finder.latest/artifacts/public/bug_fixing_commits.json.zst",
    1,
)

BUG_INTRODUCING_COMMITS_DB = "data/bug_introducing_commits.json"
db.register(
    BUG_INTRODUCING_COMMITS_DB,
    "https://index.taskcluster.net/v1/task/project.relman.bugbug_annotate.regressor_finder.latest/artifacts/public/bug_introducing_commits.json.zst",
    1,
)


BASE_URL = "https://index.taskcluster.net/v1/task/project.relman.bugbug.train_{model_name}.latest/artifacts/public/{model_name}model.zst"


def compress_file(path):
    cctx = zstandard.ZstdCompressor()
    with open(path, "rb") as input_f:
        with open(f"{path}.zst", "wb") as output_f:
            cctx.copy_stream(input_f, output_f)


def download_model(model_name):
    if not os.path.exists(f"{model_name}model"):
        url = BASE_URL.format(model_name=model_name)
        logger.info(f"Downloading {url}...")
        download_check_etag(url, f"{model_name}model.zst")
        dctx = zstandard.ZstdDecompressor()
        with open(f"{model_name}model.zst", "rb") as input_f:
            with open(f"{model_name}model", "wb") as output_f:
                dctx.copy_stream(input_f, output_f)
        assert os.path.exists(f"{model_name}model"), "Decompressed file exists"


def clone_gecko_dev(repo_dir):
    repo_url = "https://github.com/mozilla/gecko-dev"

    if not os.path.exists(repo_dir):
        retry(lambda: subprocess.run(["git", "clone", repo_url, repo_dir], check=True))

    retry(
        lambda: subprocess.run(
            ["git", "pull", repo_url, "master"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )
    )


def get_commits_to_ignore(repo_dir):
    commits_to_ignore = []

    # TODO: Make repository analyze all commits, even those to ignore, but add a field "ignore" or a function should_ignore that analyzes the commit data. This way we don't have to clone the Mercurial repository in this script.
    with hglib.open(repo_dir) as hg:
        revs = repository.get_revs(hg, -10000)

    commits = repository.hg_log_multi(repo_dir, revs)

    commits_to_ignore = []

    def append_commits_to_ignore(commits, type_):
        for commit in commits:
            commits_to_ignore.append(
                {
                    "mercurial_rev": commit.node,
                    "git_rev": vcs_map.mercurial_to_git(commit.node),
                    "type": type_,
                }
            )

    append_commits_to_ignore(
        list(repository.get_commits_to_ignore(repo_dir, commits)), ""
    )

    logger.info(
        f"{len(commits_to_ignore)} commits to ignore (excluding backed-out commits)"
    )

    append_commits_to_ignore(
        (commit for commit in commits if commit.backedoutby), "backedout"
    )

    logger.info(
        f"{len(commits_to_ignore)} commits to ignore (including backed-out commits)"
    )

    with open("commits_to_ignore.csv", "w") as f:
        writer = csv.DictWriter(f, fieldnames=["mercurial_rev", "git_rev", "type"])
        writer.writeheader()
        writer.writerows(commits_to_ignore)

    return commits_to_ignore


def find_bug_fixing_commits():
    logger.info("Downloading commits database...")
    db.download_version(repository.COMMITS_DB)
    if db.is_old_version(repository.COMMITS_DB) or not os.path.exists(
        repository.COMMITS_DB
    ):
        db.download(repository.COMMITS_DB, force=True)

    logger.info("Downloading bugs database...")
    db.download_version(bugzilla.BUGS_DB)
    if db.is_old_version(bugzilla.BUGS_DB) or not os.path.exists(bugzilla.BUGS_DB):
        db.download(bugzilla.BUGS_DB, force=True)

    logger.info("Download previous classifications...")
    db.download_version(BUG_FIXING_COMMITS_DB)
    if db.is_old_version(BUG_FIXING_COMMITS_DB) or not os.path.exists(
        BUG_FIXING_COMMITS_DB
    ):
        db.download(BUG_FIXING_COMMITS_DB, force=True)

    logger.info("Get previously classified commits...")
    prev_bug_fixing_commits = list(db.read(BUG_FIXING_COMMITS_DB))
    prev_bug_fixing_commits_nodes = set(
        bug_fixing_commit["mercurial_rev"]
        for bug_fixing_commit in prev_bug_fixing_commits
    )
    logger.info(f"Already classified {len(prev_bug_fixing_commits)} commits...")

    # TODO: Switch to the pure Defect model, as it's better in this case.
    logger.info("Downloading defect/enhancement/task model...")
    download_model("defectenhancementtask")
    defect_model = DefectEnhancementTaskModel.load("defectenhancementtaskmodel")

    logger.info("Downloading regression model...")
    download_model("regression")
    regression_model = RegressionModel.load("regressionmodel")

    start_date = datetime.now() - RELATIVE_START_DATE
    end_date = datetime.now() - RELATIVE_END_DATE
    logger.info(
        f"Gathering bug IDs associated to commits (since {start_date} and up to {end_date})..."
    )
    commit_map = defaultdict(list)
    for commit in repository.get_commits():
        if commit["node"] in prev_bug_fixing_commits_nodes:
            continue

        commit_date = dateutil.parser.parse(commit["pushdate"])
        if commit_date < start_date or commit_date > end_date:
            continue

        commit_map[commit["bug_id"]].append(commit)

    logger.info(
        f"{sum(len(commit_list) for commit_list in commit_map.values())} commits found, {len(commit_map)} bugs linked to commits"
    )
    assert len(commit_map) > 0

    def get_relevant_bugs():
        return (bug for bug in bugzilla.get_bugs() if bug["id"] in commit_map)

    bug_count = sum(1 for bug in get_relevant_bugs())
    logger.info(
        f"{bug_count} bugs in total, {len(commit_map) - bug_count} bugs linked to commits missing"
    )

    known_defect_labels = defect_model.get_labels()
    known_regression_labels = regression_model.get_labels()

    bug_fixing_commits = []

    def append_bug_fixing_commits(bug_id, type_):
        for commit in commit_map[bug_id]:
            bug_fixing_commits.append(
                {
                    "mercurial_rev": commit["node"],
                    "git_rev": vcs_map.mercurial_to_git(commit["node"]),
                    "type": type_,
                }
            )

    for bug in tqdm(get_relevant_bugs(), total=bug_count):
        # Ignore bugs which are not linked to the commits we care about.
        if bug["id"] not in commit_map:
            continue

        # If we know the label already, we don't need to apply the model.
        if (
            bug["id"] in known_regression_labels
            and known_regression_labels[bug["id"]] == 1
        ):
            append_bug_fixing_commits(bug["id"], "r")
            continue

        if bug["id"] in known_defect_labels:
            if known_defect_labels[bug["id"]] == "defect":
                append_bug_fixing_commits(bug["id"], "d")
            else:
                append_bug_fixing_commits(bug["id"], "e")
            continue

        if defect_model.classify(bug)[0] == "defect":
            if regression_model.classify(bug)[0] == 1:
                append_bug_fixing_commits(bug["id"], "r")
            else:
                append_bug_fixing_commits(bug["id"], "d")
        else:
            append_bug_fixing_commits(bug["id"], "e")

    db.append(BUG_FIXING_COMMITS_DB, bug_fixing_commits)
    compress_file(BUG_FIXING_COMMITS_DB)

    bug_fixing_commits = prev_bug_fixing_commits + bug_fixing_commits
    return [
        bug_fixing_commit
        for bug_fixing_commit in bug_fixing_commits
        if bug_fixing_commit["type"] in ["r", "d"]
    ]


def find_bug_introducing_commits(cache_dir, git_repo_dir):
    mercurial_repo_dir = os.path.join(cache_dir, "mozilla-central")

    logger.info("Downloading Mercurial <-> git mapping file...")
    vcs_map.download_mapfile()

    logger.info(f"Cloning mercurial repository to {mercurial_repo_dir}...")
    repository.clone(mercurial_repo_dir)

    logger.info(f"Cloning git repository to {git_repo_dir}...")
    clone_gecko_dev(git_repo_dir)

    logger.info("Download previously found bug-introducing commits...")
    db.download_version(BUG_INTRODUCING_COMMITS_DB)
    if db.is_old_version(BUG_INTRODUCING_COMMITS_DB) or not os.path.exists(
        BUG_INTRODUCING_COMMITS_DB
    ):
        db.download(BUG_INTRODUCING_COMMITS_DB, force=True)

    logger.info("Get previously found bug-introducing commits...")
    prev_bug_introducing_commits = list(db.read(BUG_INTRODUCING_COMMITS_DB))
    prev_bug_introducing_commits_nodes = set(
        bug_introducing_commit["bug_fixing_mercurial_rev"]
        for bug_introducing_commit in prev_bug_introducing_commits
    )
    logger.info(f"Already classified {len(prev_bug_introducing_commits)} commits...")

    commits_to_ignore = get_commits_to_ignore(mercurial_repo_dir)

    git_hashes_to_ignore = set(commit["git_rev"] for commit in commits_to_ignore)

    with open("git_hashes_to_ignore", "w") as f:
        f.writelines(f"{git_hash}\n" for git_hash in git_hashes_to_ignore)

    bug_fixing_commits = find_bug_fixing_commits()

    logger.info(f"{len(bug_fixing_commits)} commits to analyze")

    # Skip already found bug-introducing commits.
    bug_fixing_commits = [
        bug_fixing_commit
        for bug_fixing_commit in bug_fixing_commits
        if bug_fixing_commit["mercurial_rev"] not in prev_bug_introducing_commits_nodes
    ]

    logger.info(
        f"{len(bug_fixing_commits)} commits left to analyze after skipping already analyzed ones"
    )

    bug_fixing_commits = [
        bug_fixing_commit
        for bug_fixing_commit in bug_fixing_commits
        if bug_fixing_commit["git_rev"] not in git_hashes_to_ignore
    ]
    logger.info(
        f"{len(bug_fixing_commits)} commits left to analyze after skipping the ones in the ignore list"
    )

    def _init(git_repo_dir):
        global GIT_REPO
        GIT_REPO = GitRepository(git_repo_dir)

    def find_bic(bug_fixing_commit):
        logger.info("Analyzing {}...".format(bug_fixing_commit["git_rev"]))

        commit = GIT_REPO.get_commit(bug_fixing_commit["git_rev"])

        # Skip huge changes, we'll likely be wrong with them.
        if len(commit.modifications) > MAX_MODIFICATION_NUMBER:
            return [None]

        bug_introducing_modifications = GIT_REPO.get_commits_last_modified_lines(
            commit, hashes_to_ignore_path=os.path.realpath("git_hashes_to_ignore")
        )
        logger.info(bug_introducing_modifications)

        bug_introducing_commits = []
        for bug_introducing_hashes in bug_introducing_modifications.values():
            for bug_introducing_hash in bug_introducing_hashes:
                bug_introducing_commits.append(
                    {
                        "bug_fixing_mercurial_rev": bug_fixing_commit["mercurial_rev"],
                        "bug_fixing_git_rev": bug_fixing_commit["git_rev"],
                        "bug_introducing_mercurial_rev": vcs_map.git_to_mercurial(
                            bug_introducing_hash
                        ),
                        "bug_introducing_git_rev": bug_introducing_hash,
                    }
                )

        # Add an empty result, just so that we don't reanalyze this again.
        if len(bug_introducing_commits) == 0:
            bug_introducing_commits.append(
                {
                    "bug_fixing_mercurial_rev": bug_fixing_commit["mercurial_rev"],
                    "bug_fixing_git_rev": bug_fixing_commit["git_rev"],
                    "bug_introducing_mercurial_rev": "",
                    "bug_introducing_git_rev": "",
                }
            )

        return bug_introducing_commits

    with concurrent.futures.ThreadPoolExecutor(
        initializer=_init, initargs=(git_repo_dir,), max_workers=os.cpu_count() + 1
    ) as executor:
        bug_introducing_commits = executor.map(find_bic, bug_fixing_commits)
        bug_introducing_commits = tqdm(
            bug_introducing_commits, total=len(bug_fixing_commits)
        )
        bug_introducing_commits = list(
            itertools.chain.from_iterable(bug_introducing_commits)
        )

    total_results_num = len(bug_introducing_commits)
    bug_introducing_commits = list(filter(None, bug_introducing_commits))
    logger.info(
        f"Skipped {total_results_num - len(bug_introducing_commits)} commits as they were too big"
    )

    db.append(BUG_INTRODUCING_COMMITS_DB, bug_introducing_commits)
    compress_file(BUG_INTRODUCING_COMMITS_DB)


def main():
    description = "Find bug-introducing commits from bug-fixing commits"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("cache_root", help="Cache for repository clones.")
    parser.add_argument("git_repo", help="Path to the gecko-dev repository.")

    args = parser.parse_args()

    # TODO: Figure out how to use wordified repository or wordified-comment-removed repository.
    find_bug_introducing_commits(args.cache_root, args.git_repo)
