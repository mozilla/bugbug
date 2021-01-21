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
from typing import Any, Dict, List, Optional, Tuple, cast

import dateutil.parser
import requests
from tqdm import tqdm

from bugbug import bugzilla, db, phabricator, repository, test_scheduling
from bugbug.models.bugtype import bug_to_types
from bugbug.models.regressor import BUG_FIXING_COMMITS_DB, RegressorModel
from bugbug.utils import (
    download_check_etag,
    download_model,
    get_secret,
    zstd_decompress,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


PAST_REGRESSIONS_BY_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.past_bugs_by_unit.latest/artifacts/public/past_regressions_by_{dimension}.json.zst"
PAST_FIXED_BUGS_BY_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.past_bugs_by_unit.latest/artifacts/public/past_fixed_bugs_by_{dimension}.json.zst"
PAST_REGRESSION_BLOCKED_BUGS_BY_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.past_bugs_by_unit.latest/artifacts/public/past_regression_blocked_bugs_by_{dimension}.json.zst"
PAST_FIXED_BUG_BLOCKED_BUGS_BY_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.past_bugs_by_unit.latest/artifacts/public/past_fixed_bug_blocked_bugs_by_{dimension}.json.zst"


def _deduplicate(bug_summaries: List[dict]) -> List[dict]:
    seen = set()
    results = []
    for bug_summary in bug_summaries[::-1]:
        if bug_summary["id"] in seen:
            continue
        seen.add(bug_summary["id"])

        results.append(bug_summary)

    return results[::-1]


def parse_risk_band(risk_band: str) -> Tuple[str, float, float]:
    name, start, end = risk_band.split("-")
    return (name, float(start), float(end))


class LandingsRiskReportGenerator(object):
    def __init__(self, repo_dir: str) -> None:
        self.risk_bands = sorted(
            (
                parse_risk_band(risk_band)
                for risk_band in get_secret("REGRESSOR_RISK_BANDS").split(";")
            ),
            key=lambda x: x[1],
        )

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

        logger.info("Downloading revisions database...")
        assert db.download(phabricator.REVISIONS_DB)

        logger.info("Downloading bugs database...")
        assert db.download(bugzilla.BUGS_DB)

        logger.info("Download commit classifications...")
        assert db.download(BUG_FIXING_COMMITS_DB)

        self.regressor_model = cast(
            RegressorModel, RegressorModel.load(download_model("regressor"))
        )

        bugzilla.set_token(get_secret("BUGZILLA_TOKEN"))
        phabricator.set_api_key(
            get_secret("PHABRICATOR_URL"), get_secret("PHABRICATOR_TOKEN")
        )

    def get_landed_and_filed_since(self, days: int) -> List[int]:
        since = datetime.utcnow() - timedelta(days=days)

        commits = [
            commit
            for commit in repository.get_commits()
            if dateutil.parser.parse(commit["pushdate"]) >= since and commit["bug_id"]
        ]

        bug_ids = set(commit["bug_id"] for commit in commits)
        bug_ids.update(
            bug["id"]
            for bug in bugzilla.get_bugs()
            if dateutil.parser.parse(bug["creation_time"]).replace(tzinfo=None) >= since
            and bug["resolution"]
            not in [
                "INVALID",
                "WONTFIX",
                "INACTIVE",
                "DUPLICATE",
                "INCOMPLETE",
                "MOVED",
            ]
        )

        return list(bug_ids)

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

        component_team_mapping = bugzilla.get_component_team_mapping()

        # A map from bug ID to the list of commits associated to the bug (in order of landing).
        bug_to_commits = collections.defaultdict(list)

        for commit in repository.get_commits():
            bug_id = commit["bug_id"]
            if not bug_id:
                continue

            bug_to_commits[bug_id].append(commit)

        commits: List[repository.CommitDict] = sum(
            (bug_to_commits[bug_id] for bug_id in bugs), []
        )
        commit_map = {commit["node"]: commit for commit in commits}

        logger.info(f"{len(commits)} commits to analyze.")

        logger.info(f"{len(bugs)} bugs to analyze.")

        bug_map = {}
        regressor_bug_ids = set()
        for bug in bugzilla.get_bugs():
            bug_map[bug["id"]] = bug

            if len(bug["regressions"]) > 0:
                regressor_bug_ids.add(bug["id"])

        logger.info("Retrieve Phabricator revisions linked to commits...")
        revision_ids = set(
            filter(None, (repository.get_revision_id(commit) for commit in commits))
        )

        logger.info("Download revisions of interest...")
        phabricator.download_revisions(revision_ids)

        revision_map = {
            revision["id"]: revision
            for revision in phabricator.get_revisions()
            if revision["id"] in revision_ids
        }

        if meta_bugs is not None:
            blocker_to_meta = collections.defaultdict(set)
            for meta_bug in meta_bugs:
                if meta_bug not in bug_map:
                    continue

                for blocker_bug_id in bugzilla.find_blocking(
                    bug_map, bug_map[meta_bug]
                ):
                    blocker_to_meta[blocker_bug_id].add(meta_bug)

        def _download_past_bugs(url: str) -> dict:
            path = os.path.join("data", os.path.basename(url)[:-4])
            download_check_etag(url, path=f"{path}.zst")
            zstd_decompress(path)
            assert os.path.exists(path)
            with open(path, "r") as f:
                return json.load(f)

        past_regressions_by = {}
        past_fixed_bugs_by = {}
        past_regression_blocked_bugs_by = {}
        past_fixed_bug_blocked_bugs_by = {}

        for dimension in ["component", "directory", "file", "function"]:
            past_regressions_by[dimension] = _download_past_bugs(
                PAST_REGRESSIONS_BY_URL.format(dimension=dimension)
            )
            past_fixed_bugs_by[dimension] = _download_past_bugs(
                PAST_FIXED_BUGS_BY_URL.format(dimension=dimension)
            )
            past_regression_blocked_bugs_by[dimension] = _download_past_bugs(
                PAST_REGRESSION_BLOCKED_BUGS_BY_URL.format(dimension=dimension)
            )
            past_fixed_bug_blocked_bugs_by[dimension] = _download_past_bugs(
                PAST_FIXED_BUG_BLOCKED_BUGS_BY_URL.format(dimension=dimension)
            )

        path_to_component = repository.get_component_mapping()

        def get_full_component(bug):
            return "{}::{}".format(bug["product"], bug["component"])

        def histogram(components: List[str]) -> Dict[str, float]:
            counter = collections.Counter(components)
            return {
                component: count / len(components)
                for component, count in counter.most_common()
            }

        def component_histogram(bugs: List[dict]) -> Dict[str, float]:
            return histogram([bug["component"] for bug in bugs])

        def find_risk_band(risk: float) -> str:
            for name, start, end in self.risk_bands:
                if start <= risk <= end:
                    return name

            assert False

        def get_prev_bugs(
            past_bugs_by: dict, commit: repository.CommitDict, component: str = None
        ) -> List[dict]:
            paths = [
                path
                for path in commit["files"]
                if component is None
                or (
                    path.encode("utf-8") in path_to_component
                    and path_to_component[path.encode("utf-8")]
                    == component.encode("utf-8")
                )
            ]

            past_bugs = []

            for path, f_group in commit["functions"].items():
                if path not in paths:
                    continue

                if path not in past_bugs_by["function"]:
                    continue

                found = False
                for f in f_group:
                    if f["name"] not in past_bugs_by["function"][path]:
                        continue

                    found = True
                    past_bugs += past_bugs_by["function"][path][f["name"]]

                if found:
                    paths.remove(path)

            for path in paths:
                if path in past_bugs_by["file"]:
                    past_bugs += past_bugs_by["file"][path]
                    paths.remove(path)

            for path, directories in zip(paths, repository.get_directories(paths)):
                found = False
                for directory in directories:
                    if directory in past_bugs_by["directory"]:
                        found = True
                        past_bugs += past_bugs_by["directory"][directory]

                if found:
                    paths.remove(path)

            components = [
                path_to_component[path.encode("utf-8")].tobytes().decode("utf-8")
                for path in paths
                if path.encode("utf-8") in path_to_component
            ]

            for component in components:
                if component in past_bugs_by["component"]:
                    past_bugs += past_bugs_by["component"][component]

            return past_bugs

        def get_prev_bugs_stats(
            commit_group: dict,
            commit_list: List[repository.CommitDict],
            component: str = None,
        ) -> None:
            # Find previous regressions occurred in the same files as those touched by these commits.
            # And find previous bugs that were fixed by touching the same files as these commits.
            # And find previous bugs that were blocked by regressions occurred in the same files as those touched by these commits.
            # And find previous bugs that were blocked by bugs that were fixed by touching the same files as those touched by these commits.
            prev_regressions: List[Dict[str, Any]] = sum(
                (
                    get_prev_bugs(past_regressions_by, commit, component)
                    for commit in commit_list
                ),
                [],
            )
            prev_fixed_bugs: List[Dict[str, Any]] = sum(
                (
                    get_prev_bugs(past_fixed_bugs_by, commit, component)
                    for commit in commit_list
                ),
                [],
            )
            prev_regression_blocked_bugs: List[Dict[str, Any]] = sum(
                (
                    get_prev_bugs(past_regression_blocked_bugs_by, commit, component)
                    for commit in commit_list
                ),
                [],
            )
            prev_fixed_bug_blocked_bugs: List[Dict[str, Any]] = sum(
                (
                    get_prev_bugs(past_fixed_bug_blocked_bugs_by, commit, component)
                    for commit in commit_list
                ),
                [],
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

            commit_group["most_common_regression_components"] = regression_components
            # These are only used for component connections for the time being.
            if component:
                commit_group["prev_regressions"] = prev_regressions[-3:]
                commit_group["prev_fixed_bugs"] = prev_fixed_bugs[-3:]
                commit_group[
                    "prev_regression_blocked_bugs"
                ] = prev_regression_blocked_bugs[-3:]
                commit_group[
                    "prev_fixed_bug_blocked_bugs"
                ] = prev_fixed_bug_blocked_bugs[-3:]
                commit_group[
                    "most_common_fixed_bugs_components"
                ] = fixed_bugs_components
                commit_group[
                    "most_common_regression_blocked_bug_components"
                ] = regression_blocked_bug_components
                commit_group[
                    "most_common_fixed_bug_blocked_bug_components"
                ] = fixed_bug_blocked_bug_components

        def get_commit_data(commit_list: List[repository.CommitDict]) -> List[dict]:
            if len(commit_list) == 0:
                return []

            # Evaluate risk of commits associated to this bug.
            probs = self.regressor_model.classify(commit_list, probabilities=True)

            commits_data = []
            for i, commit in enumerate(commit_list):
                revision_id = repository.get_revision_id(commit)
                if revision_id in revision_map:
                    testing = phabricator.get_testing_project(revision_map[revision_id])

                    if testing is None:
                        testing = "missing"
                else:
                    testing = None

                commits_data.append(
                    {
                        "id": commit["node"],
                        "testing": testing,
                        "risk": float(probs[i][1]),
                        "backedout": bool(commit["backedoutby"]),
                        "author": commit["author_email"],
                        "reviewers": commit["reviewers"],
                        "coverage": [
                            commit["cov_added"],
                            commit["cov_covered"],
                            commit["cov_unknown"],
                        ],
                    }
                )

            return commits_data

        bug_summaries = []
        for bug_id in bugs:
            if bug_id not in bug_map:
                continue

            commit_list = bug_to_commits.get(bug_id, [])
            commit_data = get_commit_data(commit_list)

            bug = bug_map[bug_id]

            time_to_bug = None
            for regressor_bug_id in bug["regressed_by"]:
                # Get the date of the last commit in the regressor bug that landed before the regression bug.
                last_commit_date = max(
                    (
                        dateutil.parser.parse(commit["pushdate"])
                        for commit in bug_to_commits.get(regressor_bug_id, [])
                        if dateutil.parser.parse(commit["pushdate"])
                        < dateutil.parser.parse(bug["creation_time"]).replace(
                            tzinfo=None
                        )
                    ),
                    default=None,
                )

                if last_commit_date is None:
                    continue

                # Get the minimum "time to bug" (from the fix time of the closest regressor to the regression bug).
                cur_time_to_bug = (
                    dateutil.parser.parse(bug["creation_time"]).replace(tzinfo=None)
                    - last_commit_date
                ).days
                if time_to_bug is None or cur_time_to_bug < time_to_bug:
                    time_to_bug = cur_time_to_bug

            bug_summary = {
                "id": bug_id,
                "regressor": bug_id in regressor_bug_ids,
                "regression": len(bug["regressed_by"]) > 0
                or any(
                    keyword in bug["keywords"]
                    for keyword in ["regression", "talos-regression"]
                )
                or (
                    "cf_has_regression_range" in bug
                    and bug["cf_has_regression_range"] == "yes"
                ),
                "time_to_bug": time_to_bug,
                "whiteboard": bug["whiteboard"],
                "assignee": bug["assigned_to"]
                if bug["assigned_to"] != "nobody@mozilla.org"
                else None,
                "versions": bugzilla.get_fixed_versions(bug),
                "component": get_full_component(bug),
                "team": bugzilla.component_to_team(
                    component_team_mapping, bug["product"], bug["component"]
                ),
                "summary": bug["summary"],
                "fixed": bug["resolution"] == "FIXED",
                "types": bug_to_types(bug),
                "severity": bug["severity"],
                "creation_date": dateutil.parser.parse(bug["creation_time"]).strftime(
                    "%Y-%m-%d"
                ),
                "date": max(
                    dateutil.parser.parse(commit["pushdate"]) for commit in commit_list
                ).strftime("%Y-%m-%d")
                if len(commit_list) > 0
                else None,
                "commits": commit_data,
                "meta_ids": list(blocker_to_meta[bug_id]),
                "risk_band": find_risk_band(
                    max(commit["risk"] for commit in commit_data)
                )
                if len(commit_data) > 0
                else None,
            }

            get_prev_bugs_stats(bug_summary, commit_list)

            bug_summaries.append(bug_summary)

        landings_by_date = collections.defaultdict(list)
        for bug_summary in bug_summaries:
            landings_by_date[bug_summary["creation_date"]].append(bug_summary)

        with open("landings_by_date.json", "w") as f:
            output: dict = {
                "summaries": landings_by_date,
            }
            if meta_bugs is not None:
                output["featureMetaBugs"] = [
                    {"id": meta_bug, "summary": bug_map[meta_bug]["summary"]}
                    for meta_bug in meta_bugs
                ]

            json.dump(output, f)

        # Retrieve components of test failures that occurred when landing patches to fix bugs in specific components.
        component_failures = collections.defaultdict(list)

        push_data_iter, push_data_count, all_runnables = test_scheduling.get_push_data(
            "group"
        )

        for revisions, _, _, possible_regressions, likely_regressions in tqdm(
            push_data_iter(), total=push_data_count
        ):
            commit_list = [
                commit_map[revision] for revision in revisions if revision in commit_map
            ]
            if len(commit_list) == 0:
                continue

            commit_bugs = [
                bug_map[commit["bug_id"]]
                for commit in commit_list
                if commit["bug_id"] in bug_map
            ]

            components = list(set(get_full_component(bug) for bug in commit_bugs))

            groups = [
                group
                for group in list(set(possible_regressions + likely_regressions))
                if group.encode("utf-8") in path_to_component
            ]

            for group in groups:
                for component in components:
                    component_failures[component].append(
                        path_to_component[group.encode("utf-8")]
                        .tobytes()
                        .decode("utf-8")
                    )

        # Filter out commits for which we have no bugs.
        commits = [commit for commit in commits if commit["bug_id"] in bug_map]

        # Sort commits by bug component, so we can use itertools.groupby to group them by bug component.
        commits.sort(key=lambda x: get_full_component(bug_map[x["bug_id"]]))

        commit_groups = []
        for component, commit_iter in itertools.groupby(
            commits, lambda x: get_full_component(bug_map[x["bug_id"]])
        ):
            commit_group = {
                "component": component,
                "most_common_test_failure_components": histogram(
                    component_failures[component]
                )
                if component in component_failures
                else {},
            }
            get_prev_bugs_stats(commit_group, list(commit_iter), component)
            commit_groups.append(commit_group)

        with open("component_connections.json", "w") as f:
            json.dump(commit_groups, f)

        repository.close_component_mapping()


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
        bugs = landings_risk_report_generator.get_landed_and_filed_since(args.days)
    else:
        assert False

    landings_risk_report_generator.go(bugs, meta_bugs)


if __name__ == "__main__":
    main()
