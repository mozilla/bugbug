# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import logging
import pickle
from collections import deque

from tqdm import tqdm

from bugbug import bugzilla, db, repository
from bugbug.models.regressor import BUG_FIXING_COMMITS_DB
from bugbug.utils import zstd_compress

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PastBugsCollector(object):
    def __init__(self):
        logger.info("Downloading commits database...")
        assert db.download(repository.COMMITS_DB)

        logger.info("Downloading bugs database...")
        assert db.download(bugzilla.BUGS_DB)

        logger.info("Download commit classifications...")
        assert db.download(BUG_FIXING_COMMITS_DB)

    def go(self):
        logger.info(
            "Generate map of bug ID -> bug data for all bugs which were defects"
        )
        bug_fixing_commits = list(db.read(BUG_FIXING_COMMITS_DB))

        bug_fixing_commits_nodes = set(
            bug_fixing_commit["rev"]
            for bug_fixing_commit in bug_fixing_commits
            if bug_fixing_commit["type"] in ("d", "r")
        )

        logger.info(f"{bug_fixing_commits_nodes} bug-fixing commits to analyze")

        all_bug_ids = set(
            commit["bug_id"]
            for commit in repository.get_commits()
            if commit["node"] in bug_fixing_commits_nodes
        )

        bug_map = {}

        for bug in bugzilla.get_bugs():
            if bug["id"] not in all_bug_ids:
                continue

            bug_map[bug["id"]] = bug

        logger.info(
            "Generate a map from function to the three last bugs which were fixed by touching that function"
        )

        past_bugs_by_function = {}

        for commit in tqdm(repository.get_commits()):
            if commit["node"] not in bug_fixing_commits_nodes:
                continue

            if commit["bug_id"] not in bug_map:
                continue

            bug = bug_map[commit["bug_id"]]

            bug_str = "Bug {} - {}".format(bug["id"], bug["summary"])

            for path, f_group in commit["functions"].items():
                if path not in past_bugs_by_function:
                    past_bugs_by_function[path] = {}

                for f in f_group:
                    if f[0] not in past_bugs_by_function[path]:
                        bugs_deque = deque(maxlen=3)
                    else:
                        bugs_deque = past_bugs_by_function[path][f[0]]["bugs"]

                    if bug_str not in bugs_deque:
                        bugs_deque.append(bug_str)

                    past_bugs_by_function[path][f[0]] = {
                        "start": f[1],
                        "end": f[2],
                        "bugs": bugs_deque,
                    }

        with open("data/past_bugs_by_function.pickle", "wb") as f:
            pickle.dump(past_bugs_by_function, f)
        zstd_compress("data/past_bugs_by_function.pickle")


def main():
    description = "Find past bugs fixed by touching given functions"
    parser = argparse.ArgumentParser(description=description)
    parser.parse_args()

    past_bugs_collector = PastBugsCollector()
    past_bugs_collector.go()


if __name__ == "__main__":
    main()
