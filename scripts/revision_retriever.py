# -*- coding: utf-8 -*-

import argparse
from datetime import datetime, timezone
from logging import getLogger

import dateutil.parser
from dateutil.relativedelta import relativedelta

from bugbug import bugzilla, db, phabricator, repository
from bugbug.utils import get_secret, zstd_compress

logger = getLogger(__name__)


class Retriever(object):
    def retrieve_revisions(
        self,
        limit_months: int = 2,
        limit_count: int | None = None,
    ) -> None:
        """Retrieve revisions from Phabricator.

        Args:
            limit_months: The number of months to go back in time to retrieve
                revisions. The limit is based on bugs last activity date and
                commits push date.
            limit_count: Only download the N oldest revisions, used mainly for
                integration tests.
        """
        phabricator.set_api_key(
            get_secret("PHABRICATOR_URL"), get_secret("PHABRICATOR_TOKEN")
        )

        db.download(phabricator.REVISIONS_DB)

        # Get the commits DB, as we need it to get the revision IDs linked to recent commits.
        assert db.download(repository.COMMITS_DB)

        # Get the bugs DB, as we need it to get the revision IDs linked to bugs.
        assert db.download(bugzilla.BUGS_DB)

        phabricator.download_modified_revisions()

        # Get IDs of revisions linked to commits.
        start_date = datetime.now(timezone.utc) - relativedelta(months=limit_months)
        revision_ids = list(
            (
                filter(
                    None,
                    (
                        repository.get_revision_id(commit)
                        for commit in repository.get_commits()
                        if dateutil.parser.parse(commit["pushdate"]).replace(
                            tzinfo=timezone.utc
                        )
                        >= start_date
                    ),
                )
            )
        )

        # Get IDs of revisions linked to bugs.
        for bug in bugzilla.get_bugs():
            if dateutil.parser.parse(bug["last_change_time"]) < start_date:
                continue

            revision_ids += bugzilla.get_revision_ids(bug)

        if limit_count is not None:
            revision_ids = revision_ids[-limit_count:]

        phabricator.download_revisions(revision_ids)

        zstd_compress(phabricator.REVISIONS_DB)


def main() -> None:
    description = "Retrieve revisions from Phabricator"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--limit-months",
        type=int,
        default=24,
        help="The number of months to go back in time to retrieve revisions.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Only download the N oldest revisions, used mainly for integration tests",
    )

    # Parse args to show the help if `--help` is passed
    args = parser.parse_args()

    retriever = Retriever()
    retriever.retrieve_revisions(args.limit_months, args.limit)


if __name__ == "__main__":
    main()
