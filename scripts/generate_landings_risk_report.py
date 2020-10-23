# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import collections
import itertools
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import dateutil.parser
import requests

from bugbug import bugzilla, db, phabricator, repository
from bugbug.models.regressor import BUG_FIXING_COMMITS_DB
from bugbug.utils import (
    download_and_load_model,
    download_check_etag,
    get_secret,
    zstd_decompress,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


PAST_REGRESSIONS_BY_FILE_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.past_bugs_by_unit.latest/artifacts/public/past_regressions_by_file.json.zst"
PAST_FIXED_BUGS_BY_FILE_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.past_bugs_by_unit.latest/artifacts/public/past_fixed_bugs_by_file.json.zst"
PAST_REGRESSION_BLOCKED_BUGS_BY_FILE_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.past_bugs_by_unit.latest/artifacts/public/past_regression_blocked_bugs_by_file.json.zst"
PAST_FIXED_BUG_BLOCKED_BUGS_BY_FILE_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.past_bugs_by_unit.latest/artifacts/public/past_fixed_bug_blocked_bugs_by_file.json.zst"
PAST_REGRESSIONS_BY_FUNCTION_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.past_bugs_by_unit.latest/artifacts/public/past_regressions_by_function.json.zst"
PAST_FIXED_BUGS_BY_FUNCTION_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.past_bugs_by_unit.latest/artifacts/public/past_fixed_bugs_by_function.json.zst"
PAST_REGRESSION_BLOCKED_BUGS_BY_FUNCTION_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.past_bugs_by_unit.latest/artifacts/public/past_regression_blocked_bugs_by_function.json.zst"
PAST_FIXED_BUG_BLOCKED_BUGS_BY_FUNCTION_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.past_bugs_by_unit.latest/artifacts/public/past_fixed_bug_blocked_bugs_by_function.json.zst"


def _deduplicate(bug_summaries: List[dict]) -> List[dict]:
    seen = set()
    results = []
    for bug_summary in bug_summaries[::-1]:
        if bug_summary["id"] in seen:
            continue
        seen.add(bug_summary["id"])

        results.append(bug_summary)

    return results[::-1]


class LandingsRiskReportGenerator(object):
    def __init__(self, repo_dir: str) -> None:
        repository.clone(repo_dir)

        logger.info("Downloading commits database...")
        assert db.download(repository.COMMITS_DB, support_files_too=True)

        logger.info("Updating commits DB...")
        for commit in repository.get_commits():
            pass

        repository.download_commits(
            repo_dir,
            rev_start="children({})".format(commit["node"]),
        )

        logger.info("Downloading bugs database...")
        assert db.download(bugzilla.BUGS_DB)

        logger.info("Download commit classifications...")
        assert db.download(BUG_FIXING_COMMITS_DB)

        self.regressor_model = download_and_load_model("regressor")

        bugzilla.set_token(get_secret("BUGZILLA_TOKEN"))
        phabricator.set_api_key(
            get_secret("PHABRICATOR_URL"), get_secret("PHABRICATOR_TOKEN")
        )

    def get_landed_since(self, days: int) -> List[int]:
        since = datetime.utcnow() - timedelta(days=days)

        commits = [
            commit
            for commit in repository.get_commits()
            if dateutil.parser.parse(commit["pushdate"]) >= since and commit["bug_id"]
        ]

        return [commit["bug_id"] for commit in commits]

    def get_regressors_of(self, bug_ids: List[int]) -> List[int]:
        bugzilla.download_bugs(bug_ids)
        return sum(
            (
                bug["regressed_by"]
                for bug in bugzilla.get_bugs()
                if bug["id"] in bug_ids
            ),
            [],
        )

    def get_blocking_of(self, bug_ids: List[int]) -> List[int]:
        bugzilla.download_bugs(bug_ids)
        bug_map = {bug["id"]: bug for bug in bugzilla.get_bugs()}
        return sum(
            (bugzilla.find_blocking(bug_map, bug_map[bug_id]) for bug_id in bug_ids), []
        )

    def get_meta_bugs(self, days: int) -> List[int]:
        params = {
            "columnlist": "bug_id",
            "order": "bug_id",
            "keywords": "feature-testing-meta",
            "keywords_type": "allwords",
            "resolution": "---",
            "f1": "anything",
            "o1": "changedafter",
            "v1": "-90d",
            "ctype": "csv",
        }

        r = requests.get("https://bugzilla.mozilla.org/buglist.cgi", params=params)
        r.raise_for_status()

        return [int(b) for b in r.text.splitlines()[1:]]

    def go(self, bugs: List[int], meta_bugs: Optional[List[int]] = None) -> None:
        if meta_bugs is not None:
            bugs += meta_bugs + self.get_blocking_of(meta_bugs)

        logger.info("Download bugs of interest...")
        bugzilla.download_bugs(bugs)

        bugs_set = set(bugs)

        commits = [
            commit
            for commit in repository.get_commits()
            if commit["bug_id"] in bugs_set
        ]
        hash_to_rev = {commit["node"]: i for i, commit in enumerate(commits)}

        logger.info(f"{len(commits)} commits to analyze.")

        bug_ids = {commit["bug_id"] for commit in commits}

        logger.info(f"{len(bug_ids)} bugs to analyze.")

        bug_map = {
            bug["id"]: bug for bug in bugzilla.get_bugs() if bug["id"] in bugs_set
        }

        logger.info("Retrieve Phabricator revisions linked to commits...")
        revisions = list(
            filter(None, (repository.get_revision_id(commit) for commit in commits))
        )
        revision_map = phabricator.get(revisions)

        if meta_bugs is not None:
            blocker_to_meta = collections.defaultdict(set)
            for meta_bug in meta_bugs:
                if meta_bug not in bug_map:
                    continue

                for blocker_bug_id in bugzilla.find_blocking(
                    bug_map, bug_map[meta_bug]
                ):
                    blocker_to_meta[blocker_bug_id].add(meta_bug)

        # TODO: Use past regressions by function information too (maybe first by function and if no results by file? or prioritize function and recentness?)

        def _download_past_bugs(url: str) -> dict:
            path = os.path.join("data", os.path.basename(url)[:-4])
            download_check_etag(url, path=f"{path}.zst")
            zstd_decompress(path)
            assert os.path.exists(path)
            with open(path, "r") as f:
                return json.load(f)

        past_regressions_by_file = _download_past_bugs(PAST_REGRESSIONS_BY_FILE_URL)
        past_fixed_bugs_by_file = _download_past_bugs(PAST_FIXED_BUGS_BY_FILE_URL)
        past_regression_blocked_bugs_by_file = _download_past_bugs(
            PAST_REGRESSION_BLOCKED_BUGS_BY_FILE_URL
        )
        past_fixed_bug_blocked_bugs_by_file = _download_past_bugs(
            PAST_FIXED_BUG_BLOCKED_BUGS_BY_FILE_URL
        )

        def component_histogram(bugs: List[dict]) -> Dict[str, float]:
            counter = collections.Counter(bug["component"] for bug in bugs)
            return {
                component: count / len(bugs)
                for component, count in counter.most_common()
            }

        # Sort commits by bug ID, so we can use itertools.groupby to group them by bug ID.
        commits.sort(key=lambda x: x["bug_id"])

        commit_groups = []
        for bug_id, commit_iter in itertools.groupby(commits, lambda x: x["bug_id"]):
            # TODO: Figure out what to do with bugs we couldn't download (security bugs).
            if bug_id not in bug_map:
                continue

            commit_list = list(commit_iter)
            commit_list.sort(key=lambda x: hash_to_rev[x["node"]])

            # Find previous regressions occurred in the same files as those touched by these commits.
            # And find previous bugs that were fixed by touching the same files as these commits.
            # And find previous bugs that were blocked by regressions occurred in the same files as those touched by these commits.
            # And find previous bugs that were blocked by bugs that were fixed by touching the same files as those touched by these commits.
            prev_regressions: List[Dict[str, Any]] = []
            prev_fixed_bugs: List[Dict[str, Any]] = []
            prev_regression_blocked_bugs: List[Dict[str, Any]] = []
            prev_fixed_bug_blocked_bugs: List[Dict[str, Any]] = []
            for commit in commit_list:
                for path in commit["files"]:
                    if path in past_regressions_by_file:
                        prev_regressions.extend(
                            bug_summary
                            for bug_summary in past_regressions_by_file[path]
                        )

                    if path in past_fixed_bugs_by_file:
                        prev_fixed_bugs.extend(
                            bug_summary for bug_summary in past_fixed_bugs_by_file[path]
                        )

                    if path in past_regression_blocked_bugs_by_file:
                        prev_regression_blocked_bugs.extend(
                            bug_summary
                            for bug_summary in past_regression_blocked_bugs_by_file[
                                path
                            ]
                        )

                    if path in past_fixed_bug_blocked_bugs_by_file:
                        prev_fixed_bug_blocked_bugs.extend(
                            bug_summary
                            for bug_summary in past_fixed_bug_blocked_bugs_by_file[path]
                        )

            prev_regressions = _deduplicate(prev_regressions)
            prev_fixed_bugs = _deduplicate(prev_fixed_bugs)
            prev_regression_blocked_bugs = _deduplicate(prev_regression_blocked_bugs)
            prev_fixed_bug_blocked_bugs = _deduplicate(prev_fixed_bug_blocked_bugs)

            regression_components = component_histogram(prev_regressions)
            fixed_bugs_components = component_histogram(prev_fixed_bugs)
            regression_blocked_bug_components = component_histogram(
                prev_regression_blocked_bugs
            )
            fixed_bug_blocked_bug_components = component_histogram(
                prev_fixed_bug_blocked_bugs
            )

            # Evaluate risk of commits associated to this bug.
            probs = self.regressor_model.classify(commit_list, probabilities=True)

            commit_groups.append(
                {
                    "id": bug_id,
                    "summary": bug_map[bug_id]["summary"],
                    "date": max(
                        dateutil.parser.parse(commit["pushdate"])
                        for commit in commit_list
                    ).strftime("%Y-%m-%d"),
                    "commits": [
                        {
                            "id": commit["node"],
                            "testing": phabricator.get_testing_projects(
                                revision_map[repository.get_revision_id(commit)]
                            )
                            if repository.get_revision_id(commit) in revision_map
                            else None,
                            "risk": float(probs[i][1]),
                        }
                        for i, commit in enumerate(commit_list)
                    ],
                    "meta_ids": list(blocker_to_meta[bug_id]),
                    "prev_regressions": prev_regressions[-3:],
                    "prev_fixed_bugs": prev_fixed_bugs[-3:],
                    "prev_regression_blocked_bugs": prev_regression_blocked_bugs[-3:],
                    "prev_fixed_bug_blocked_bugs": prev_fixed_bug_blocked_bugs[-3:],
                    "most_common_regression_components": regression_components,
                    "most_common_fixed_bugs_components": fixed_bugs_components,
                    "most_common_regression_blocked_bug_components": regression_blocked_bug_components,
                    "most_common_fixed_bug_blocked_bug_components": fixed_bug_blocked_bug_components,
                }
            )

        landings_by_date = collections.defaultdict(list)
        for commit_group in commit_groups:
            landings_by_date[commit_group["date"]].append(commit_group)

        with open("landings_by_date.json", "w") as f:
            json.dump(landings_by_date, f)


def main() -> None:
    description = "Generate risk report of recent landings"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("repo_dir", help="Path to a Gecko repository.")
    parser.add_argument(
        "--bugs",
        type=int,
        nargs="*",
        help="Which bugs to analyze.",
    )
    parser.add_argument(
        "--regressors-of",
        type=int,
        nargs="*",
        help="List of bugs whose regressors have to be analyzed.",
    )
    parser.add_argument(
        "--blocking-of",
        type=int,
        nargs="*",
        help="List of bugs whose blockers have to be analyzed.",
    )
    parser.add_argument(
        "--meta-bugs",
        type=int,
        nargs="*",
        help="Analyze all bugs blocking meta bugs changed since a given number of days ago.",
    )
    parser.add_argument(
        "--days",
        type=int,
        help="How many days of commits to analyze.",
    )
    args = parser.parse_args()

    landings_risk_report_generator = LandingsRiskReportGenerator(args.repo_dir)

    meta_bugs: Optional[List[int]] = None
    if args.meta_bugs is not None:
        meta_bugs = landings_risk_report_generator.get_meta_bugs(args.days)

    if args.bugs is not None:
        bugs = args.bugs
    elif args.regressors_of is not None:
        bugs = landings_risk_report_generator.get_regressors_of(args.regressors_of)
    elif args.blocking_of is not None:
        bugs = landings_risk_report_generator.get_blocking_of(args.blocking_of)
    elif args.days is not None:
        bugs = landings_risk_report_generator.get_landed_since(args.days)
    else:
        assert False

    landings_risk_report_generator.go(bugs, meta_bugs)


if __name__ == "__main__":
    main()
