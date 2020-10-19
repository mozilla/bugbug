# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import collections
import itertools
import logging
import os
from datetime import datetime, timedelta
from typing import Collection

import dateutil.parser

from bugbug import db, phabricator, repository
from bugbug.utils import get_secret

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestingPolicyStatsGenerator(object):
    def __init__(self, repo_dir: str) -> None:
        if not os.path.exists(repo_dir):
            repository.clone(repo_dir)
        else:
            repository.pull(repo_dir, "mozilla-central", "tip")

        logger.info("Downloading commits database...")
        assert db.download(repository.COMMITS_DB, support_files_too=True)

        logger.info("Updating commits DB...")
        for commit in repository.get_commits():
            pass

        repository.download_commits(
            repo_dir,
            rev_start="children({})".format(commit["node"]),
        )

        phabricator.set_api_key(
            get_secret("PHABRICATOR_URL"), get_secret("PHABRICATOR_TOKEN")
        )

    def get_landed_since(self, days: int) -> Collection[repository.CommitDict]:
        since = datetime.utcnow() - timedelta(days=days)

        return [
            commit
            for commit in repository.get_commits()
            if dateutil.parser.parse(commit["pushdate"]) >= since
        ]

    def go(self, days: int) -> None:
        commits = self.get_landed_since(days)

        logger.info("Retrieve Phabricator revisions linked to commits...")
        revision_ids = list(
            filter(None, (repository.get_revision_id(commit) for commit in commits))
        )
        revisions_map = phabricator.get(revision_ids)

        testing_projects = list(
            itertools.chain(
                *(
                    phabricator.get_testing_projects(revision)
                    for revision in revisions_map.values()
                )
            )
        )

        counter = collections.Counter(testing_projects)

        for testing_project, count in counter.most_common():
            print(
                f"{testing_project} - {round(100 * count / len(testing_projects), 1)}%"
            )


def main() -> None:
    description = "Report statistics about selected testing tags"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("repo_dir", help="Path to a Gecko repository.")
    parser.add_argument(
        "days",
        type=int,
        help="How many days of commits to analyze.",
    )
    args = parser.parse_args()

    testing_policy_stats_generator = TestingPolicyStatsGenerator(args.repo_dir)
    testing_policy_stats_generator.go(args.days)


if __name__ == "__main__":
    main()
