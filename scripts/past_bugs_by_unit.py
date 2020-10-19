# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import json
import logging
from collections import defaultdict
from typing import Dict, List

from tqdm import tqdm

from bugbug import bugzilla, db, repository
from bugbug.models.regressor import BUG_FIXING_COMMITS_DB
from bugbug.utils import zstd_compress

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PastBugsCollector(object):
    def __init__(self) -> None:
        logger.info("Downloading commits database...")
        assert db.download(repository.COMMITS_DB)

        logger.info("Downloading bugs database...")
        assert db.download(bugzilla.BUGS_DB)

        logger.info("Download commit classifications...")
        assert db.download(BUG_FIXING_COMMITS_DB)

    def go(self) -> None:
        logger.info(
            "Generate map of bug ID -> bug data for all bugs which were defects"
        )
        bug_fixing_commits = list(db.read(BUG_FIXING_COMMITS_DB))

        bug_fixing_commits_nodes = set(
            bug_fixing_commit["rev"]
            for bug_fixing_commit in bug_fixing_commits
            if bug_fixing_commit["type"] in ("d", "r")
        )

        logger.info(f"{len(bug_fixing_commits_nodes)} bug-fixing commits to analyze")

        all_bug_ids = set(
            commit["bug_id"]
            for commit in repository.get_commits()
            if commit["node"] in bug_fixing_commits_nodes
        )

        bug_map = {
            bug["id"]: bug for bug in bugzilla.get_bugs() if bug["id"] in all_bug_ids
        }

        logger.info(
            "Generate a map from files/functions to the bugs which were fixed/introduced by touching them"
        )

        # TODO: Support "moving" past bugs between files when they are renamed and between functions when they are
        # moved across files.

        past_regressions_by_file: Dict[str, List[int]] = defaultdict(list)
        past_fixed_bugs_by_file: Dict[str, List[int]] = defaultdict(list)
        past_regression_blocked_bugs_by_file: Dict[str, List[int]] = defaultdict(list)
        past_fixed_bug_blocked_bugs_by_file: Dict[str, List[int]] = defaultdict(list)
        past_regressions_by_function: Dict[str, Dict[str, List[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        past_fixed_bugs_by_function: Dict[str, Dict[str, List[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        past_regression_blocked_bugs_by_function: Dict[
            str, Dict[str, List[int]]
        ] = defaultdict(lambda: defaultdict(list))
        past_fixed_bug_blocked_bugs_by_function: Dict[
            str, Dict[str, List[int]]
        ] = defaultdict(lambda: defaultdict(list))

        for commit in tqdm(repository.get_commits()):
            if commit["bug_id"] not in bug_map:
                continue

            bug = bug_map[commit["bug_id"]]

            if len(bug["regressions"]) > 0:
                for path in commit["files"]:
                    past_regressions_by_file[path].extend(
                        bug_id for bug_id in bug["regressions"] if bug_id in bug_map
                    )

                    past_regression_blocked_bugs_by_file[path].extend(
                        bugzilla.find_blocked_by(bug_map, bug)
                    )

                for path, f_group in commit["functions"].items():
                    for f in f_group:
                        past_regressions_by_function[path][f[0]].extend(
                            bug_id for bug_id in bug["regressions"] if bug_id in bug_map
                        )

                        past_regression_blocked_bugs_by_function[path][f[0]].extend(
                            bugzilla.find_blocked_by(bug_map, bug)
                        )

            if commit["node"] in bug_fixing_commits_nodes:
                for path in commit["files"]:
                    past_fixed_bugs_by_file[path].append(bug["id"])

                    past_fixed_bug_blocked_bugs_by_file[path].extend(
                        bugzilla.find_blocked_by(bug_map, bug)
                    )

                for path, f_group in commit["functions"].items():
                    for f in f_group:
                        past_fixed_bugs_by_function[path][f[0]].append(bug["id"])

                        past_fixed_bug_blocked_bugs_by_function[path][f[0]].extend(
                            bugzilla.find_blocked_by(bug_map, bug)
                        )

        def _transform(bug_ids: List[int]) -> List[dict]:
            seen = set()
            results = []
            for bug_id in bug_ids:
                if bug_id in seen:
                    continue
                seen.add(bug_id)

                bug = bug_map[bug_id]
                results.append(
                    {
                        "id": bug_id,
                        "summary": bug["summary"],
                        "product": bug["product"],
                        "component": bug["component"],
                    }
                )

            return results

        past_regression_summaries_by_file = {
            path: _transform(bug_ids)
            for path, bug_ids in past_regressions_by_file.items()
        }
        past_fixed_bug_summaries_by_file = {
            path: _transform(bug_ids)
            for path, bug_ids in past_fixed_bugs_by_file.items()
        }
        past_regression_blocked_bug_summaries_by_file = {
            path: _transform(bug_ids)
            for path, bug_ids in past_regression_blocked_bugs_by_file.items()
        }
        past_fixed_bug_blocked_bug_summaries_by_file = {
            path: _transform(bug_ids)
            for path, bug_ids in past_fixed_bug_blocked_bugs_by_file.items()
        }
        past_regression_summaries_by_function = {
            path: {func: _transform(bug_ids) for func, bug_ids in funcs_bugs.items()}
            for path, funcs_bugs in past_regressions_by_function.items()
        }
        past_fixed_bug_summaries_by_function = {
            path: {func: _transform(bug_ids) for func, bug_ids in funcs_bugs.items()}
            for path, funcs_bugs in past_fixed_bugs_by_function.items()
        }
        past_regression_blocked_bug_summaries_by_function = {
            path: {func: _transform(bug_ids) for func, bug_ids in funcs_bugs.items()}
            for path, funcs_bugs in past_regression_blocked_bugs_by_function.items()
        }
        past_fixed_bug_blocked_bug_summaries_by_function = {
            path: {func: _transform(bug_ids) for func, bug_ids in funcs_bugs.items()}
            for path, funcs_bugs in past_fixed_bug_blocked_bugs_by_function.items()
        }

        with open("data/past_regressions_by_file.json", "w") as f:
            json.dump(past_regression_summaries_by_file, f)
        zstd_compress("data/past_regressions_by_file.json")

        with open("data/past_fixed_bugs_by_file.json", "w") as f:
            json.dump(past_fixed_bug_summaries_by_file, f)
        zstd_compress("data/past_fixed_bugs_by_file.json")

        with open("data/past_regression_blocked_bugs_by_file.json", "w") as f:
            json.dump(past_regression_blocked_bug_summaries_by_file, f)
        zstd_compress("data/past_regression_blocked_bugs_by_file.json")

        with open("data/past_fixed_bug_blocked_bugs_by_file.json", "w") as f:
            json.dump(past_fixed_bug_blocked_bug_summaries_by_file, f)
        zstd_compress("data/past_fixed_bug_blocked_bugs_by_file.json")

        with open("data/past_regressions_by_function.json", "w") as f:
            json.dump(past_regression_summaries_by_function, f)
        zstd_compress("data/past_regressions_by_function.json")

        with open("data/past_fixed_bugs_by_function.json", "w") as f:
            json.dump(past_fixed_bug_summaries_by_function, f)
        zstd_compress("data/past_fixed_bugs_by_function.json")

        with open("data/past_regression_blocked_bugs_by_function.json", "w") as f:
            json.dump(past_regression_blocked_bug_summaries_by_function, f)
        zstd_compress("data/past_regression_blocked_bugs_by_function.json")

        with open("data/past_fixed_bug_blocked_bugs_by_function.json", "w") as f:
            json.dump(past_fixed_bug_blocked_bug_summaries_by_function, f)
        zstd_compress("data/past_fixed_bug_blocked_bugs_by_function.json")


def main() -> None:
    description = "Find past bugs linked to given units of source code"
    parser = argparse.ArgumentParser(description=description)
    parser.parse_args()

    past_bugs_collector = PastBugsCollector()
    past_bugs_collector.go()


if __name__ == "__main__":
    main()
