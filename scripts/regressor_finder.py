# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import os
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime
from logging import INFO, basicConfig, getLogger

import dateutil.parser
import hglib
import tenacity
from dateutil.relativedelta import relativedelta
from libmozdata import vcs_map
from microannotate import utils as microannotate_utils
from tqdm import tqdm

from bugbug import bugzilla, db, repository
from bugbug.models.regressor import (
    BUG_FIXING_COMMITS_DB,
    BUG_INTRODUCING_COMMITS_DB,
    TOKENIZED_BUG_INTRODUCING_COMMITS_DB,
)
from bugbug.utils import (
    ThreadPoolExecutorResult,
    download_and_load_model,
    zstd_compress,
)

basicConfig(level=INFO)
logger = getLogger(__name__)

thread_local = threading.local()

MAX_MODIFICATION_NUMBER = 50
RELATIVE_START_DATE = relativedelta(years=2, months=6)
# Only needed because mercurial<->git mapping could be behind.
RELATIVE_END_DATE = relativedelta(days=7)

IGNORED_COMMITS_DB = "data/ignored_commits.json"
db.register(
    IGNORED_COMMITS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/ignored_commits.json.zst",
    1,
)


class RegressorFinder(object):
    def __init__(
        self,
        mercurial_repo_dir=None,
        git_repo_url=None,
        git_repo_dir=None,
        tokenized_git_repo_url=None,
        tokenized_git_repo_dir=None,
    ):
        self.mercurial_repo_dir = mercurial_repo_dir
        self.git_repo_url = git_repo_url
        self.git_repo_dir = git_repo_dir
        self.tokenized_git_repo_url = tokenized_git_repo_url
        self.tokenized_git_repo_dir = tokenized_git_repo_dir

        with ThreadPoolExecutorResult(max_workers=3) as executor:
            if self.mercurial_repo_dir is not None:
                logger.info(
                    f"Cloning mercurial repository to {self.mercurial_repo_dir}..."
                )
                executor.submit(repository.clone, self.mercurial_repo_dir)

            if self.git_repo_url is not None:
                logger.info(f"Cloning {self.git_repo_url} to {self.git_repo_dir}...")
                executor.submit(
                    self.clone_git_repo, self.git_repo_url, self.git_repo_dir
                )

            if self.tokenized_git_repo_url is not None:
                logger.info(
                    f"Cloning {self.tokenized_git_repo_url} to {self.tokenized_git_repo_dir}..."
                )
                executor.submit(
                    self.clone_git_repo,
                    self.tokenized_git_repo_url,
                    self.tokenized_git_repo_dir,
                )

        logger.info(f"Initializing mapping between git and mercurial commits...")
        self.init_mapping()

    def clone_git_repo(self, repo_url, repo_dir):
        if not os.path.exists(repo_dir):
            tenacity.retry(
                lambda: subprocess.run(
                    ["git", "clone", "--quiet", repo_url, repo_dir], check=True
                ),
                wait=tenacity.wait_fixed(30),
                stop=tenacity.stop_after_attempt(5),
            )()

            logger.info(f"{repo_dir} cloned")

        logger.info(f"Pulling and updating {repo_dir}")

        tenacity.retry(
            lambda: subprocess.run(
                ["git", "pull", "--quiet", repo_url, "master"],
                cwd=repo_dir,
                capture_output=True,
                check=True,
            ),
            wait=tenacity.wait_fixed(30),
            stop=tenacity.stop_after_attempt(5),
        )()

        logger.info(f"{repo_dir} pulled and updated")

    def init_mapping(self):
        logger.info("Downloading Mercurial <-> git mapping file...")
        vcs_map.download_mapfile()
        vcs_map.load_mapfile()

        if self.tokenized_git_repo_url is not None:
            (
                self.tokenized_git_to_mercurial,
                self.mercurial_to_tokenized_git,
            ) = microannotate_utils.get_commit_mapping(self.tokenized_git_repo_dir)

    # TODO: Make repository module analyze all commits, even those to ignore, but add a field "ignore" or a function should_ignore that analyzes the commit data. This way we don't have to clone the Mercurial repository in this script.
    def get_commits_to_ignore(self):
        logger.info("Download previous commits to ignore...")
        db.download(IGNORED_COMMITS_DB)

        logger.info("Get previously classified commits...")
        prev_commits_to_ignore = list(db.read(IGNORED_COMMITS_DB))
        logger.info(f"Already found {len(prev_commits_to_ignore)} commits to ignore...")

        # When we already have some analyzed commits, re-analyze the last 3500 to make sure
        # we didn't miss back-outs that happened since the last analysis.
        if len(prev_commits_to_ignore) > 0:
            first_commit_to_reanalyze = (
                -3500 if len(prev_commits_to_ignore) >= 3500 else 0
            )
            rev_start = "children({})".format(
                prev_commits_to_ignore[first_commit_to_reanalyze]["rev"]
            )
        else:
            rev_start = 0

        with hglib.open(self.mercurial_repo_dir) as hg:
            revs = repository.get_revs(hg, rev_start)

        # Drop commits which are not yet present in the mercurial <-> git mapping.
        while len(revs) > 0:
            try:
                vcs_map.mercurial_to_git(revs[-1].decode("ascii"))
                break
            except Exception as e:
                if not str(e).startswith("Missing mercurial commit in the VCS map"):
                    raise

                revs.pop()

        commits = repository.hg_log_multi(self.mercurial_repo_dir, revs)

        repository.set_commits_to_ignore(self.mercurial_repo_dir, commits)

        chosen_commits = set()
        commits_to_ignore = []
        for commit in commits:
            if commit.ignored or commit.backedoutby:
                commits_to_ignore.append(
                    {
                        "rev": commit.node,
                        "type": "backedout" if commit.backedoutby else "",
                    }
                )
                chosen_commits.add(commit.node)

        logger.info(f"{len(commits_to_ignore)} new commits to ignore...")

        for prev_commit in prev_commits_to_ignore[::-1]:
            if prev_commit["rev"] not in chosen_commits:
                commits_to_ignore.append(prev_commit)
                chosen_commits.add(prev_commit["rev"])

        logger.info(f"{len(commits_to_ignore)} commits to ignore...")

        logger.info(
            "...of which {} are backed-out".format(
                sum(1 for commit in commits_to_ignore if commit["type"] == "backedout")
            )
        )

        db.write(IGNORED_COMMITS_DB, commits_to_ignore)
        zstd_compress(IGNORED_COMMITS_DB)
        db.upload(IGNORED_COMMITS_DB)

    def find_bug_fixing_commits(self):
        logger.info("Downloading commits database...")
        assert db.download(repository.COMMITS_DB)

        logger.info("Downloading bugs database...")
        assert db.download(bugzilla.BUGS_DB)

        logger.info("Download previous classifications...")
        db.download(BUG_FIXING_COMMITS_DB)

        logger.info("Get previously classified commits...")
        prev_bug_fixing_commits = list(db.read(BUG_FIXING_COMMITS_DB))
        prev_bug_fixing_commits_nodes = set(
            bug_fixing_commit["rev"] for bug_fixing_commit in prev_bug_fixing_commits
        )
        logger.info(f"Already classified {len(prev_bug_fixing_commits)} commits...")

        # TODO: Switch to the pure Defect model, as it's better in this case.
        logger.info("Downloading defect/enhancement/task model...")
        defect_model = download_and_load_model("defectenhancementtask")

        logger.info("Downloading regression model...")
        regression_model = download_and_load_model("regression")

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

            commit_map[commit["bug_id"]].append(commit["node"])

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
                bug_fixing_commits.append({"rev": commit, "type": type_})

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
        zstd_compress(BUG_FIXING_COMMITS_DB)
        db.upload(BUG_FIXING_COMMITS_DB)

    def find_bug_introducing_commits(self, repo_dir, tokenized):
        from pydriller import GitRepository
        from pydriller.domain.commit import ModificationType

        logger.info("Download commits to ignore...")
        assert db.download(IGNORED_COMMITS_DB)
        commits_to_ignore = list(db.read(IGNORED_COMMITS_DB))

        logger.info("Download bug-fixing classifications...")
        assert db.download(BUG_FIXING_COMMITS_DB)
        bug_fixing_commits = [
            bug_fixing_commit
            for bug_fixing_commit in db.read(BUG_FIXING_COMMITS_DB)
            if bug_fixing_commit["type"] in ["r", "d"]
        ]

        if tokenized:
            db_path = TOKENIZED_BUG_INTRODUCING_COMMITS_DB
        else:
            db_path = BUG_INTRODUCING_COMMITS_DB

        def git_to_mercurial(rev):
            if tokenized:
                return self.tokenized_git_to_mercurial[rev]
            else:
                return vcs_map.git_to_mercurial(rev)

        def mercurial_to_git(rev):
            if tokenized:
                return self.mercurial_to_tokenized_git[rev]
            else:
                return vcs_map.mercurial_to_git(rev)

        logger.info("Download previously found bug-introducing commits...")
        db.download(db_path)

        logger.info("Get previously found bug-introducing commits...")
        prev_bug_introducing_commits = list(db.read(db_path))
        prev_bug_introducing_commits_nodes = set(
            bug_introducing_commit["bug_fixing_rev"]
            for bug_introducing_commit in prev_bug_introducing_commits
        )
        logger.info(
            f"Already classified {len(prev_bug_introducing_commits)} commits..."
        )

        hashes_to_ignore = set(commit["rev"] for commit in commits_to_ignore)

        with open("git_hashes_to_ignore", "w") as f:
            f.writelines(
                "{}\n".format(mercurial_to_git(commit["rev"]))
                for commit in commits_to_ignore
                if not tokenized or commit["rev"] in self.mercurial_to_tokenized_git
            )

        logger.info(f"{len(bug_fixing_commits)} commits to analyze")

        # Skip already found bug-introducing commits.
        bug_fixing_commits = [
            bug_fixing_commit
            for bug_fixing_commit in bug_fixing_commits
            if bug_fixing_commit["rev"] not in prev_bug_introducing_commits_nodes
        ]

        logger.info(
            f"{len(bug_fixing_commits)} commits left to analyze after skipping already analyzed ones"
        )

        bug_fixing_commits = [
            bug_fixing_commit
            for bug_fixing_commit in bug_fixing_commits
            if bug_fixing_commit["rev"] not in hashes_to_ignore
        ]
        logger.info(
            f"{len(bug_fixing_commits)} commits left to analyze after skipping the ones in the ignore list"
        )

        if tokenized:
            bug_fixing_commits = [
                bug_fixing_commit
                for bug_fixing_commit in bug_fixing_commits
                if bug_fixing_commit["rev"] in self.mercurial_to_tokenized_git
            ]
            logger.info(
                f"{len(bug_fixing_commits)} commits left to analyze after skipping the ones with no git hash"
            )

        git_init_lock = threading.Lock()

        def _init(git_repo_dir):
            with git_init_lock:
                thread_local.git = GitRepository(git_repo_dir)
                # Call get_head in order to make pydriller initialize the repository.
                thread_local.git.get_head()

        def find_bic(bug_fixing_commit):
            logger.info("Analyzing {}...".format(bug_fixing_commit["rev"]))

            git_fix_revision = mercurial_to_git(bug_fixing_commit["rev"])

            commit = thread_local.git.get_commit(git_fix_revision)

            # Skip huge changes, we'll likely be wrong with them.
            if len(commit.modifications) > MAX_MODIFICATION_NUMBER:
                logger.info(
                    "Skipping {} as it is too big".format(bug_fixing_commit["rev"])
                )
                return None

            def get_modification_path(mod):
                path = mod.new_path
                if (
                    mod.change_type == ModificationType.RENAME
                    or mod.change_type == ModificationType.DELETE
                ):
                    path = mod.old_path
                return path

            bug_introducing_modifications = {}
            for modification in commit.modifications:
                if (
                    get_modification_path(modification)
                    == "testing/web-platform/meta/MANIFEST.json"
                ):
                    continue

                bug_introducing_modifications.update(
                    thread_local.git.get_commits_last_modified_lines(
                        commit,
                        modification=modification,
                        hashes_to_ignore_path=os.path.realpath("git_hashes_to_ignore"),
                    )
                )

            logger.info(
                "Found {} for {}".format(
                    bug_introducing_modifications, bug_fixing_commit["rev"]
                )
            )

            bug_introducing_commits = []
            for bug_introducing_hashes in bug_introducing_modifications.values():
                for bug_introducing_hash in bug_introducing_hashes:
                    try:
                        bug_introducing_commits.append(
                            {
                                "bug_fixing_rev": bug_fixing_commit["rev"],
                                "bug_introducing_rev": git_to_mercurial(
                                    bug_introducing_hash
                                ),
                            }
                        )
                    except Exception as e:
                        # Skip commits that are in git but not in mercurial, as they are too old (older than "Free the lizard").
                        if not str(e).startswith("Missing git commit in the VCS map"):
                            raise

            # Add an empty result, just so that we don't reanalyze this again.
            if len(bug_introducing_commits) == 0:
                bug_introducing_commits.append(
                    {
                        "bug_fixing_rev": bug_fixing_commit["rev"],
                        "bug_introducing_rev": "",
                    }
                )

            return bug_introducing_commits

        def compress_and_upload():
            zstd_compress(db_path)
            db.upload(db_path)

        workers = os.cpu_count() + 1
        logger.info(
            f"Analyzing {len(bug_fixing_commits)} commits using {workers} workers..."
        )

        with concurrent.futures.ThreadPoolExecutor(
            initializer=_init, initargs=(repo_dir,), max_workers=workers
        ) as executor:

            def results():
                start_time = time.monotonic()

                futures = {
                    executor.submit(find_bic, bug_fixing_commit): bug_fixing_commit[
                        "rev"
                    ]
                    for bug_fixing_commit in bug_fixing_commits
                }

                for future in tqdm(
                    concurrent.futures.as_completed(futures), total=len(futures),
                ):
                    exc = future.exception()
                    if exc is not None:
                        logger.info(
                            f"Exception {exc} while analyzing {futures[future]}"
                        )
                        for f in futures:
                            f.cancel()

                    result = future.result()
                    if result is not None:
                        yield from result

                    if time.monotonic() - start_time >= 3600:
                        compress_and_upload()
                        start_time = time.monotonic()

            db.append(db_path, results())

        compress_and_upload()


def evaluate(bug_introducing_commits):
    logger.info("Downloading commits database...")
    assert db.download(repository.COMMITS_DB)

    logger.info("Downloading bugs database...")
    assert db.download(bugzilla.BUGS_DB)

    logger.info("Building bug -> commits map...")
    bug_to_commits_map = defaultdict(list)
    for commit in tqdm(repository.get_commits()):
        bug_to_commits_map[commit["bug_id"]].append(commit["node"])

    logger.info("Loading known regressors using regressed-by information...")
    known_regressors = {}
    for bug in tqdm(bugzilla.get_bugs()):
        if bug["regressed_by"]:
            known_regressors[bug["id"]] = bug["regressed_by"]
    logger.info(f"Loaded {len(known_regressors)} known regressors")

    fix_to_regressors_map = defaultdict(list)
    for bug_introducing_commit in bug_introducing_commits:
        if not bug_introducing_commit["bug_introducing_rev"]:
            continue

        fix_to_regressors_map[bug_introducing_commit["bug_fixing_rev"]].append(
            bug_introducing_commit["bug_introducing_rev"]
        )

    logger.info(f"{len(fix_to_regressors_map)} fixes linked to regressors")
    logger.info(
        f"{sum(len(regressors) for regressors in fix_to_regressors_map.values())} regressors linked to fixes"
    )

    logger.info("Measuring how many known regressors SZZ was able to find correctly...")
    all_regressors = 0
    perfect_regressors = 0
    found_regressors = 0
    misassigned_regressors = 0
    for bug_id, regressor_bugs in tqdm(known_regressors.items()):
        # Get all commits which fixed the bug.
        fix_commits = bug_to_commits_map[bug_id] if bug_id in bug_to_commits_map else []
        if len(fix_commits) == 0:
            continue

        # Skip bug/regressor when we didn't analyze the commits to fix the bug (as
        # certainly we can't have found the regressor in this case).
        if not any(fix_commit in fix_to_regressors_map for fix_commit in fix_commits):
            continue

        # Get all commits linked to the regressor bug.
        regressor_commits = []
        for regressor_bug in regressor_bugs:
            if regressor_bug not in bug_to_commits_map:
                continue

            regressor_commits += (
                commit for commit in bug_to_commits_map[regressor_bug]
            )

        if len(regressor_commits) == 0:
            continue

        found_good = False
        found_bad = False
        for fix_commit in fix_commits:
            # Check if we found at least a correct regressor.
            if any(
                regressor_commit in regressor_commits
                for regressor_commit in fix_to_regressors_map[fix_commit]
            ):
                found_good = True

            # Check if we found at least a wrong regressor.
            if any(
                regressor_commit not in regressor_commits
                for regressor_commit in fix_to_regressors_map[fix_commit]
            ):
                found_bad = True

        all_regressors += 1

        if found_good and not found_bad:
            perfect_regressors += 1
        if found_good:
            found_regressors += 1
        if found_bad:
            misassigned_regressors += 1

    logger.info(
        f"Perfectly found {perfect_regressors} regressors out of {all_regressors}"
    )
    logger.info(f"Found {found_regressors} regressors out of {all_regressors}")
    logger.info(
        f"Misassigned {misassigned_regressors} regressors out of {all_regressors}"
    )


def main():
    description = "Find bug-introducing commits from bug-fixing commits"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("what", choices=["to_ignore", "bug_fixing", "bug_introducing"])
    parser.add_argument(
        "--repo_dir",
        help="Path to a Gecko repository. If no repository exists, it will be cloned to this location.",
    )
    parser.add_argument(
        "--git_repo_url", help="URL to the git repository on which to run SZZ."
    )
    parser.add_argument(
        "--git_repo_dir", help="Path where the git repository will be cloned."
    )
    parser.add_argument(
        "--tokenized_git_repo_url",
        help="URL to the tokenized git repository on which to run SZZ.",
    )
    parser.add_argument(
        "--tokenized_git_repo_dir",
        help="Path where the tokenized git repository will be cloned.",
    )

    args = parser.parse_args()

    regressor_finder = RegressorFinder(
        args.repo_dir,
        args.git_repo_url,
        args.git_repo_dir,
        args.tokenized_git_repo_url,
        args.tokenized_git_repo_dir,
    )

    if args.what == "to_ignore":
        regressor_finder.get_commits_to_ignore()
    elif args.what == "bug_fixing":
        regressor_finder.find_bug_fixing_commits()
    elif args.what == "bug_introducing":
        assert args.git_repo_url or args.tokenized_git_repo_url

        if args.git_repo_url:
            assert not args.tokenized_git_repo_url
            regressor_finder.find_bug_introducing_commits(args.git_repo_dir, False)
            evaluate(db.read(BUG_INTRODUCING_COMMITS_DB))

        if args.tokenized_git_repo_url:
            assert not args.git_repo_url
            regressor_finder.find_bug_introducing_commits(
                args.tokenized_git_repo_dir, True
            )
            evaluate(db.read(TOKENIZED_BUG_INTRODUCING_COMMITS_DB))


if __name__ == "__main__":
    main()
