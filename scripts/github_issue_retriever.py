# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
from logging import getLogger

from bugbug import db, github
from bugbug.utils import zstd_compress

logger = getLogger(__name__)


class Retriever(object):
    def retrieve_issues(
        self, owner: str, repo: str, state: str, retrieve_events: bool
    ) -> None:

        last_modified = None
        db.download(github.GITHUB_ISSUES_DB)

        try:
            last_modified = db.last_modified(github.GITHUB_ISSUES_DB)
        except Exception:
            pass

        if last_modified:
            logger.info(
                f"Retrieving issues modified or created since the last run on {last_modified.isoformat()}"
            )
            data = github.fetch_issues_updated_since_timestamp(
                owner, repo, state, last_modified.isoformat(), retrieve_events
            )

            updated_ids = set(issue["id"] for issue in data)

            logger.info(
                "Deleting issues that were changed since the last run and saving updates"
            )
            github.delete_issues(lambda issue: issue["id"] in updated_ids)

            db.append(github.GITHUB_ISSUES_DB, data)
            logger.info("Updating finished")
        else:
            logger.info("Retrieving all issues since last_modified is not available")
            github.download_issues(owner, repo, state, retrieve_events)

        zstd_compress(github.GITHUB_ISSUES_DB)


def main() -> None:
    description = "Retrieve GitHub issues"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--owner",
        help="GitHub repository owner.",
        type=str,
        required=True,
    )
    parser.add_argument(
        "--repo",
        help="GitHub repository name.",
        type=str,
        required=True,
    )
    parser.add_argument(
        "--state",
        type=str,
        default="all",
        help="Indicates the state of the issues to return. Can be either open, closed, or all",
    )
    parser.add_argument(
        "--retrieve-events",
        action="store_true",
        help="Whether to retrieve events for each issue.",
    )

    # Parse args to show the help if `--help` is passed
    args = parser.parse_args()

    retriever = Retriever()
    retriever.retrieve_issues(args.owner, args.repo, args.state, args.retrieve_events)


if __name__ == "__main__":
    main()
