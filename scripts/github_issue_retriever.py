# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
from logging import getLogger

import requests

from bugbug import db
from bugbug.github import Github, IssueDict
from bugbug.utils import extract_private, zstd_compress

logger = getLogger(__name__)


class Retriever(object):
    def __init__(
        self,
        owner: str,
        repo: str,
        state: str,
        retrieve_events: bool,
        retrieve_private: bool,
    ):
        self.owner = owner
        self.repo = repo
        self.state = state
        self.retrieve_events = retrieve_events
        self.retrieve_private = retrieve_private
        self.github = Github(
            owner=owner, repo=repo, state=state, retrieve_events=retrieve_events
        )

    def replace_with_private(
        self, original_data: list[IssueDict]
    ) -> tuple[list[IssueDict], set]:
        """Replace title and body of automatically closed public issues.

        Replace them with title and body of a corresponding private issue
        to account for moderation workflow in webcompat repository
        """
        updated_ids = set()
        updated_issues = []
        for item in original_data:
            if item["title"] == "Issue closed.":
                extracted = extract_private(item["body"])
                if extracted is None:
                    continue

                owner, repo, issue_number = extracted
                try:
                    private_issue = self.github.fetch_issue_by_number(
                        owner, repo, issue_number
                    )

                    if private_issue:
                        item["title"] = private_issue["title"]
                        item["body"] = private_issue["body"]
                        updated_ids.add(item["id"])
                        updated_issues.append(item)

                except requests.HTTPError as e:
                    if e.response.status_code == 410:
                        logger.info(e)
                    else:
                        raise

        return updated_issues, updated_ids

    def retrieve_issues(self) -> None:

        last_modified = None
        db.download(self.github.db_path)

        try:
            last_modified = db.last_modified(self.github.db_path)
        except db.LastModifiedNotAvailable:
            pass

        if last_modified:
            logger.info(
                f"Retrieving issues modified or created since the last run on {last_modified.isoformat()}"
            )
            data = self.github.fetch_issues_updated_since_timestamp(
                last_modified.isoformat()
            )

            if self.retrieve_private:
                logger.info(
                    "Replacing contents of auto closed public issues with private issues content"
                )
                self.replace_with_private(data)

            updated_ids = set(issue["id"] for issue in data)

            logger.info(
                "Deleting issues that were changed since the last run and saving updates"
            )
            self.github.delete_issues(lambda issue: issue["id"] in updated_ids)

            db.append(self.github.db_path, data)
            logger.info("Updating finished")
        else:
            logger.info("Retrieving all issues since last_modified is not available")
            self.github.download_issues()

            if self.retrieve_private:
                logger.info(
                    "Replacing contents of auto closed public issues with private issues content"
                )

                all_issues = list(self.github.get_issues())
                updated_issues, updated_ids = self.replace_with_private(all_issues)

                logger.info(
                    "Deleting public issues that were updated and saving updates"
                )
                self.github.delete_issues(lambda issue: issue["id"] in updated_ids)
                db.append(self.github.db_path, updated_issues)

        zstd_compress(self.github.db_path)


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
    parser.add_argument(
        "--retrieve-private",
        action="store_true",
        help="Whether to retrieve private issue content (only webcompat repository usecase).",
    )

    # Parse args to show the help if `--help` is passed
    args = parser.parse_args()

    retriever = Retriever(
        args.owner, args.repo, args.state, args.retrieve_events, args.retrieve_private
    )
    retriever.retrieve_issues()


if __name__ == "__main__":
    main()
