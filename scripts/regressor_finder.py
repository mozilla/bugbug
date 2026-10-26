# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import os
import threading
import time
from collections import defaultdict
from datetime import datetime
from logging import INFO, basicConfig, getLogger
from typing import Iterator, cast

import dateutil.parser
import hglib
from dateutil.relativedelta import relativedelta
from tqdm import tqdm

from bugbug import bugzilla, db, labels, repository
from bugbug.models.defect_enhancement_task import DefectEnhancementTaskModel
from bugbug.models.regression import RegressionModel
from bugbug.models.regressor import BUG_FIXING_COMMITS_DB, BUG_INTRODUCING_COMMITS_DB
from bugbug.utils import download_model, zstd_compress

basicConfig(level=INFO)
logger = getLogger(__name__)

thread_local = threading.local()

MAX_MODIFICATION_NUMBER = 50
# Leave some margin before the task deadline to upload the results.
TIME_BUDGET = 20 * 3600
RELATIVE_START_DATE = relativedelta(years=2)
RELATIVE_END_DATE = relativedelta(days=7)

IGNORED_COMMITS_DB = "data/ignored_commits.json"
db.register(
    IGNORED_COMMITS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/ignored_commits.json.zst",
    5,
)


def is_useless_line(line: str) -> bool:
    line = line.strip()
    return (
        not line
        or line == "#"
        or line.startswith(("# ", "//", "/*", "*", "'''", '"""'))
    )


def get_deleted_lines(patch: bytes) -> dict[str, list[int]]:
    """Get the lines deleted or modified by a patch, for each file it modifies.

    The patch must be a git-style diff without context lines (i.e. -U0). Line
    numbers refer to the files before the patch is applied.
    """
    deleted_lines: dict[str, list[int]] = {}
    path = None
    in_header = False
    lineno = 0
    for line in patch.decode("utf-8", "replace").split("\n"):
        if line.startswith("diff --git "):
            path = None
            in_header = True
        elif in_header and line.startswith("--- "):
            if line != "--- /dev/null":
                path = line[len("--- a/") :]
                deleted_lines[path] = []
        elif line.startswith("@@ "):
            in_header = False
            # e.g. "@@ -12,3 +12,4 @@" or "@@ -12 +12 @@"
            lineno = int(line.split(" ")[1][1:].split(",")[0])
        elif not in_header and path is not None and line.startswith("-"):
            if not is_useless_line(line[1:]):
                deleted_lines[path].append(lineno)
            lineno += 1

    return deleted_lines


def find_bug_introducing_revs(
    hg: hglib.client.hgclient,
    rev: str,
    ignored_revs_by_path: dict[str, list[str]],
) -> set[str]:
    """Find the commits which last touched the lines deleted or modified by a commit (SZZ)."""
    patch = hg.rawcommand(hglib.util.cmdbuilder(b"diff", c=rev, git=True, unified=0))

    # Skip huge changes, we'll likely be wrong with them.
    num_files = patch.startswith(b"diff --git ") + patch.count(b"\ndiff --git ")
    if num_files > MAX_MODIFICATION_NUMBER:
        logger.info("Skipping %s as it is too big", rev)
        return set()

    bug_introducing_revs: set[str] = set()
    for path, linenos in get_deleted_lines(patch).items():
        if not linenos or path == "testing/web-platform/meta/MANIFEST.json":
            continue

        # Don't try to find the bug-introducing commit for modifications
        # in the bug-fixing commit to non-source code files.
        if repository.get_type(path) not in repository.SOURCE_CODE_TYPES_TO_EXT:
            continue

        skip = " + ".join(ignored_revs_by_path.get(path, [])) or None
        annotate = hg.rawcommand(
            hglib.util.cmdbuilder(
                b"annotate",
                b"path:" + path.encode("utf-8"),
                r=f"p1({rev})",
                w=True,
                skip=skip,
                T="{lines % '{node}\\n'}",
            )
        )
        line_revs = annotate.decode("ascii").splitlines()
        bug_introducing_revs.update(
            line_revs[lineno - 1] for lineno in linenos if lineno <= len(line_revs)
        )

    return bug_introducing_revs


class RegressorFinder(object):
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

        logger.info("%d commits to ignore...", len(commits_to_ignore))

        # Skip backed-out commits which aren't in the repository (commits which landed *before* the Mercurial history
        # started, and backouts which mentioned a bad hash in their message).
        commits_to_ignore = [
            c for c in commits_to_ignore if c["rev"][:12] in all_commits
        ]

        logger.info("%d commits to ignore...", len(commits_to_ignore))

        logger.info(
            "...of which %d are backed-out",
            sum(commit["type"] == "backedout" for commit in commits_to_ignore),
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
            "Already classified %d commits...", len(prev_bug_fixing_commits_nodes)
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
            "Gathering bug IDs associated to commits (since %s and up to %s)...",
            start_date,
            end_date,
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
            "%d commits found, %d bugs linked to commits",
            sum(len(commit_list) for commit_list in commit_map.values()),
            len(commit_map),
        )
        assert len(commit_map) > 0

        def get_relevant_bugs() -> Iterator[dict]:
            return (bug for bug in bugzilla.get_bugs() if bug["id"] in commit_map)

        bug_count = sum(1 for bug in get_relevant_bugs())
        logger.info(
            "%d bugs in total, %d bugs linked to commits missing",
            bug_count,
            len(commit_map) - bug_count,
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

    def find_bug_introducing_commits(self, repo_dir: str) -> None:
        start_time = time.monotonic()

        logger.info("Cloning Mercurial repository to %s...", repo_dir)
        repository.clone(repo_dir)

        logger.info("Download commits to ignore...")
        assert db.download(IGNORED_COMMITS_DB)
        ignored_revs = {commit["rev"] for commit in db.read(IGNORED_COMMITS_DB)}

        logger.info("Download bug-fixing classifications...")
        assert db.download(BUG_FIXING_COMMITS_DB)
        bug_fixing_revs = [
            bug_fixing_commit["rev"]
            for bug_fixing_commit in db.read(BUG_FIXING_COMMITS_DB)
            if bug_fixing_commit["type"] in ["r", "d"]
        ]

        logger.info("Download previously found bug-introducing commits...")
        db.download(BUG_INTRODUCING_COMMITS_DB)
        prev_bug_fixing_revs = {
            bug_introducing_commit["bug_fixing_rev"]
            for bug_introducing_commit in db.read(BUG_INTRODUCING_COMMITS_DB)
        }
        logger.info("Already classified %d commits...", len(prev_bug_fixing_revs))

        logger.info("%d commits to analyze", len(bug_fixing_revs))
        bug_fixing_revs = [
            rev
            for rev in bug_fixing_revs
            if rev not in prev_bug_fixing_revs and rev not in ignored_revs
        ]
        logger.info(
            "%d commits left to analyze after skipping already analyzed and ignored ones",
            len(bug_fixing_revs),
        )

        # Collect, for each file touched by a bug-fixing commit, the ignored commits
        # which touched it, so that annotate can skip over them.
        logger.info("Download commits...")
        assert db.download(repository.COMMITS_DB)

        bug_fixing_revs_set = set(bug_fixing_revs)
        bug_fixing_paths = set()
        for commit in repository.get_commits(
            include_no_bug=True, include_backouts=True, include_ignored=True
        ):
            if commit["node"] in bug_fixing_revs_set:
                bug_fixing_paths.update(commit["files"])

        ignored_revs_by_path: dict[str, list[str]] = defaultdict(list)
        for commit in repository.get_commits(
            include_no_bug=True, include_backouts=True, include_ignored=True
        ):
            if commit["node"] in ignored_revs:
                for path in commit["files"]:
                    if path in bug_fixing_paths:
                        ignored_revs_by_path[path].append(commit["node"])

        def find_bic(rev: str) -> list[dict]:
            bug_introducing_revs = find_bug_introducing_revs(
                thread_local.hg, rev, ignored_revs_by_path
            )

            # Add an empty result, just so that we don't reanalyze this again.
            return [
                {"bug_fixing_rev": rev, "bug_introducing_rev": bug_introducing_rev}
                for bug_introducing_rev in sorted(bug_introducing_revs) or [""]
            ]

        def _init(repo_dir: str) -> None:
            thread_local.hg = hglib.open(repo_dir)

        def append_compress_and_upload(results: list[dict]) -> None:
            db.append(BUG_INTRODUCING_COMMITS_DB, results)
            zstd_compress(BUG_INTRODUCING_COMMITS_DB)
            db.upload(BUG_INTRODUCING_COMMITS_DB)

        workers = (os.cpu_count() or 1) + 1
        logger.info(
            "Analyzing %d commits using %d workers...", len(bug_fixing_revs), workers
        )

        results: list[dict] = []
        last_upload_time = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(
            initializer=_init, initargs=(repo_dir,), max_workers=workers
        ) as executor:
            futures = {executor.submit(find_bic, rev): rev for rev in bug_fixing_revs}

            for future in tqdm(
                concurrent.futures.as_completed(futures), total=len(futures)
            ):
                try:
                    results += future.result()
                except hglib.error.CommandError:
                    logger.exception("Failed to analyze %s", futures[future])

                if time.monotonic() - last_upload_time < 3600:
                    continue

                append_compress_and_upload(results)
                results = []
                last_upload_time = time.monotonic()

                # Stop early, so that the remaining commits are analyzed in the next run
                # instead of losing everything when the task times out.
                if time.monotonic() - start_time >= TIME_BUDGET:
                    logger.info("Time budget exhausted, stopping")
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

        append_compress_and_upload(results)


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
    logger.info("Loaded %d known regressors", len(known_regressors))

    fix_to_regressors_map = defaultdict(list)
    for bug_introducing_commit in bug_introducing_commits:
        if not bug_introducing_commit["bug_introducing_rev"]:
            continue

        fix_to_regressors_map[bug_introducing_commit["bug_fixing_rev"]].append(
            bug_introducing_commit["bug_introducing_rev"]
        )

    logger.info("%d fixes linked to regressors", len(fix_to_regressors_map))
    logger.info(
        "%d regressors linked to fixes",
        sum(len(regressors) for regressors in fix_to_regressors_map.values()),
    )

    logger.info("Measuring how many known regressors SZZ was able to find correctly...")
    all_regressors = 0
    perfect_regressors = 0
    found_regressors = 0
    misassigned_regressors = 0
    for bug_id, regressor_bugs in tqdm(known_regressors.items()):
        # Get all commits which fixed the bug.
        fix_commits = bug_to_commits_map.get(bug_id, [])
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
        "Perfectly found %d regressors out of %d", perfect_regressors, all_regressors
    )
    logger.info("Found %d regressors out of %d", found_regressors, all_regressors)
    logger.info(
        "Misassigned %d regressors out of %d", misassigned_regressors, all_regressors
    )


def main() -> None:
    description = "Find bug-introducing commits from bug-fixing commits"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("what", choices=["to_ignore", "bug_fixing", "bug_introducing"])
    parser.add_argument(
        "--repo_dir",
        help="Path to the Mercurial repository on which to run SZZ (it will be cloned if missing).",
    )

    args = parser.parse_args()

    regressor_finder = RegressorFinder()

    if args.what == "to_ignore":
        regressor_finder.get_commits_to_ignore()
    elif args.what == "bug_fixing":
        regressor_finder.find_bug_fixing_commits()
    elif args.what == "bug_introducing":
        assert args.repo_dir
        regressor_finder.find_bug_introducing_commits(args.repo_dir)
        evaluate(db.read(BUG_INTRODUCING_COMMITS_DB))


if __name__ == "__main__":
    main()
