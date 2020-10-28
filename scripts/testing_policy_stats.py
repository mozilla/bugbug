# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import collections
import logging
import os
from datetime import datetime, timedelta
from typing import Collection, Iterable

import dateutil.parser

from bugbug import bugzilla, db, phabricator, repository
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

        logger.info("Downloading revisions database...")
        assert db.download(phabricator.REVISIONS_DB)

        logger.info("Downloading bugs database...")
        assert db.download(bugzilla.BUGS_DB)

        phabricator.set_api_key(
            get_secret("PHABRICATOR_URL"), get_secret("PHABRICATOR_TOKEN")
        )

    def get_landed_since(
        self, days_start: int, days_end: int
    ) -> Collection[repository.CommitDict]:
        since = datetime.utcnow() - timedelta(days=days_start)
        until = datetime.utcnow() - timedelta(days=days_end)

        return [
            commit
            for commit in repository.get_commits(
                include_no_bug=True, include_backouts=True, include_ignored=True
            )
            if since <= dateutil.parser.parse(commit["pushdate"]) <= until
        ]

    def go(self, days_start: int, days_end: int) -> None:
        commits = self.get_landed_since(days_start, days_end)

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

        logger.info("Download bugs of interest...")
        bugzilla.download_bugs(
            commit["bug_id"] for commit in commits if commit["bug_id"]
        )

        # Filter-out commits with no Phabricator revision linked to them, or with no testing tags.
        commits = [
            commit
            for commit in commits
            if repository.get_revision_id(commit) in revision_map
        ]
        logger.info(f"{len(commits)} revisions")

        # Filter-out commits with no testing tags.
        commits = [
            commit
            for commit in commits
            if phabricator.get_testing_project(
                revision_map[repository.get_revision_id(commit)]
            )
            is not None
        ]
        logger.info(f"{len(commits)} revisions with testing tags")

        def list_testing_projects(
            commits: Iterable[repository.CommitDict],
        ) -> Collection[str]:
            return list(
                filter(
                    None,
                    (
                        phabricator.get_testing_project(
                            revision_map[repository.get_revision_id(commit)]
                        )
                        for commit in commits
                    ),
                )
            )

        testing_projects = list_testing_projects(commits)

        print(f"Most common testing tags (in {len(commits)} revisions):")
        for testing_project, count in collections.Counter(
            testing_projects
        ).most_common():
            print(
                f"{testing_project} - {round(100 * count / len(testing_projects), 1)}%"
            )

        backedout_commits = [commit for commit in commits if commit["backedoutby"]]
        backedout_testing_projects = list_testing_projects(backedout_commits)

        print(
            f"\nMost common testing tags for backed-out revisions (in {len(backedout_commits)} revisions):"
        )
        for testing_project, count in collections.Counter(
            backedout_testing_projects
        ).most_common():
            print(
                f"{testing_project} - {round(100 * count / len(backedout_testing_projects), 1)}%"
            )

        regressor_bug_ids = {
            bug["id"] for bug in bugzilla.get_bugs() if len(bug["regressions"]) > 0
        }

        regressor_commits = [
            commit for commit in commits if commit["bug_id"] in regressor_bug_ids
        ]
        regressor_testing_projects = list_testing_projects(regressor_commits)

        print(
            f"\nMost common testing tags for revisions which caused regressions (in {len(regressor_commits)} revisions):"
        )
        for testing_project, count in collections.Counter(
            regressor_testing_projects
        ).most_common():
            print(
                f"{testing_project} - {round(100 * count / len(regressor_testing_projects), 1)}%"
            )


def main() -> None:
    description = "Report statistics about selected testing tags"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("repo_dir", help="Path to a Gecko repository.")
    parser.add_argument(
        "days_start",
        type=int,
        help="First day of commits to analyze.",
    )
    parser.add_argument(
        "days_end",
        type=int,
        help="Last day of commits to analyze.",
    )
    args = parser.parse_args()

    testing_policy_stats_generator = TestingPolicyStatsGenerator(args.repo_dir)
    testing_policy_stats_generator.go(args.days_start, args.days_end)


if __name__ == "__main__":
    main()
