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
from typing import Iterator, cast

import dateutil.parser
import tenacity
from dateutil.relativedelta import relativedelta
from libmozdata import vcs_map
from microannotate import utils as microannotate_utils
from tqdm import tqdm

from bugbug import bugzilla, db, labels, repository
from bugbug.models.defect_enhancement_task import DefectEnhancementTaskModel
from bugbug.models.regression import RegressionModel
from bugbug.models.regressor import (
    BUG_FIXING_COMMITS_DB,
    BUG_INTRODUCING_COMMITS_DB,
    TOKENIZED_BUG_INTRODUCING_COMMITS_DB,
)
from bugbug.utils import ThreadPoolExecutorResult, download_model, zstd_compress

basicConfig(level=INFO)
logger = getLogger(__name__)

thread_local = threading.local()

MAX_MODIFICATION_NUMBER = 50
RELATIVE_START_DATE = relativedelta(years=2)
# Only needed because mercurial<->git mapping could be behind.
RELATIVE_END_DATE = relativedelta(days=7)

IGNORED_COMMITS_DB = "data/ignored_commits.json"
db.register(
    IGNORED_COMMITS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/ignored_commits.json.zst",
    5,
)


class RegressorFinder(object):
    def __init__(
        self,
        git_repo_url=None,
        git_repo_dir=None,
        tokenized_git_repo_url=None,
        tokenized_git_repo_dir=None,
    ):
        self.git_repo_url = git_repo_url
        self.git_repo_dir = git_repo_dir
        self.tokenized_git_repo_url = tokenized_git_repo_url
        self.tokenized_git_repo_dir = tokenized_git_repo_dir

        with ThreadPoolExecutorResult(max_workers=3) as executor:
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

        logger.info("Initializing mapping between git and mercurial commits...")
        self.init_mapping()

    def clone_git_repo(self, repo_url, repo_dir):
        if not os.path.exists(repo_dir):
            tenacity.retry(
                wait=tenacity.wait_exponential(multiplier=1, min=16, max=64),
                stop=tenacity.stop_after_attempt(5),
            )(
                lambda: subprocess.run(
                    ["git", "clone", "--quiet", repo_url, repo_dir], check=True
                )
            )()

            logger.info(f"{repo_dir} cloned")

        logger.info(f"Fetching {repo_dir}")

        tenacity.retry(
            wait=tenacity.wait_exponential(multiplier=1, min=16, max=64),
            stop=tenacity.stop_after_attempt(5),
        )(
            lambda: subprocess.run(
                ["git", "fetch", "--quiet"],
                cwd=repo_dir,
                capture_output=True,
                check=True,
            )
        )()

        logger.info(f"{repo_dir} fetched")

    def init_mapping(self):
        if self.tokenized_git_repo_url is not None:
            (
                self.tokenized_git_to_mercurial,
                self.mercurial_to_tokenized_git,
            ) = microannotate_utils.get_commit_mapping(self.tokenized_git_repo_dir)

    def get_commits_to_ignore(self) -> None:
        assert db.download(repository.COMMITS_DB)

        ignored = set()
        commits_to_ignore = []
        all_commits = set()

        annotate_ignore_nodes = {
            node for node, label in labels.get_labels("annotateignore") if label == "1"
        }

        for commit in repository.get_commits(
            include_no_bug=True, include_backouts=True, include_ignored=True
        ):
            all_commits.add(commit["node"][:12])

            if (
                commit["ignored"]
                or commit["backedoutby"]
                or not commit["bug_id"]
                or len(commit["backsout"]) > 0
                or repository.is_wptsync(commit)
                or commit["node"] in annotate_ignore_nodes
            ):
                commits_to_ignore.append(
                    {
                        "rev": commit["node"],
                        "type": "backedout" if commit["backedoutby"] else "",
                    }
                )
                ignored.add(commit["node"][:12])

            if len(commit["backsout"]) > 0:
                for backedout in commit["backsout"]:
                    if backedout[:12] in ignored:
                        continue
                    ignored.add(backedout[:12])

                    commits_to_ignore.append({"rev": backedout, "type": "backedout"})

        logger.info(f"{len(commits_to_ignore)} commits to ignore...")

        # Skip backed-out commits which aren't in the repository (commits which landed *before* the Mercurial history
        # started, and backouts which mentioned a bad hash in their message).
        commits_to_ignore = [
            c for c in commits_to_ignore if c["rev"][:12] in all_commits
        ]

        logger.info(f"{len(commits_to_ignore)} commits to ignore...")

        logger.info(
            "...of which {} are backed-out".format(
                sum(1 for commit in commits_to_ignore if commit["type"] == "backedout")
            )
        )

        db.write(IGNORED_COMMITS_DB, commits_to_ignore)
        zstd_compress(IGNORED_COMMITS_DB)
        db.upload(IGNORED_COMMITS_DB)

    def find_bug_fixing_commits(self) -> None:
        logger.info("Downloading commits database...")
        assert db.download(repository.COMMITS_DB)

        logger.info("Downloading bugs database...")
        assert db.download(bugzilla.BUGS_DB)

        logger.info("Download previous classifications...")
        db.download(BUG_FIXING_COMMITS_DB)

        logger.info("Get previously classified commits...")
        prev_bug_fixing_commits_nodes = set(
            bug_fixing_commit["rev"]
            for bug_fixing_commit in db.read(BUG_FIXING_COMMITS_DB)
        )
        logger.info(
            f"Already classified {len(prev_bug_fixing_commits_nodes)} commits..."
        )

        # TODO: Switch to the pure Defect model, as it's better in this case.
        logger.info("Downloading defect/enhancement/task model...")
        defect_model = cast(
            DefectEnhancementTaskModel,
            DefectEnhancementTaskModel.load(download_model("defectenhancementtask")),
        )

        logger.info("Downloading regression model...")
        regression_model = cast(
            RegressionModel, RegressionModel.load(download_model("regression"))
        )

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

        def get_relevant_bugs() -> Iterator[dict]:
            return (bug for bug in bugzilla.get_bugs() if bug["id"] in commit_map)

        bug_count = sum(1 for bug in get_relevant_bugs())
        logger.info(
            f"{bug_count} bugs in total, {len(commit_map) - bug_count} bugs linked to commits missing"
        )

        known_defect_labels, _ = defect_model.get_labels()
        known_regression_labels, _ = regression_model.get_labels()

        bug_fixing_commits = []
        bugs_to_classify = []

        def append_bug_fixing_commits(bug_id: int, type_: str) -> None:
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

            bugs_to_classify.append(bug)

        classified_bugs = []
        if bugs_to_classify:
            classified_bugs = defect_model.classify(bugs_to_classify)

        defect_bugs = []

        for bug, label in zip(bugs_to_classify, classified_bugs):
            if label == "defect":
                defect_bugs.append(bug)
            else:
                append_bug_fixing_commits(bug["id"], "e")

        classified_defect_bugs = []
        if defect_bugs:
            classified_defect_bugs = regression_model.classify(defect_bugs)

        for bug, classification in zip(defect_bugs, classified_defect_bugs):
            if classification == 1:
                append_bug_fixing_commits(bug["id"], "r")
            else:
                append_bug_fixing_commits(bug["id"], "d")

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

        def git_to_mercurial(revs):
            if tokenized:
                return (self.tokenized_git_to_mercurial[rev] for rev in revs)
            else:
                yield from vcs_map.git_to_mercurial(repo_dir, revs)

        def mercurial_to_git(revs):
            if tokenized:
                return (self.mercurial_to_tokenized_git[rev] for rev in revs)
            else:
                yield from vcs_map.mercurial_to_git(repo_dir, revs)

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
            git_hashes = mercurial_to_git(
                commit["rev"]
                for commit in tqdm(commits_to_ignore)
                if not tokenized or commit["rev"] in self.mercurial_to_tokenized_git
            )
            f.writelines("{}\n".format(git_hash) for git_hash in git_hashes)

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

            git_fix_revision = tuple(mercurial_to_git([bug_fixing_commit["rev"]]))[0]

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
                path = get_modification_path(modification)

                if path == "testing/web-platform/meta/MANIFEST.json":
                    continue

                # Don't try to find the bug-introducing commit for modifications
                # in the bug-fixing commit to non-source code files.
                if repository.get_type(path) not in repository.SOURCE_CODE_TYPES_TO_EXT:
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
                                "bug_introducing_rev": tuple(
                                    git_to_mercurial([bug_introducing_hash])
                                )[0],
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
                    concurrent.futures.as_completed(futures),
                    total=len(futures),
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


def main() -> None:
    description = "Find bug-introducing commits from bug-fixing commits"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("what", choices=["to_ignore", "bug_fixing", "bug_introducing"])
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
