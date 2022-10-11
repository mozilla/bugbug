# -*- coding: utf-8 -*-

import argparse
from datetime import datetime, timezone
from logging import getLogger
from typing import Optional

import dateutil.parser
from dateutil.relativedelta import relativedelta

from bugbug import bugzilla, db, phabricator, repository
from bugbug.utils import get_secret, zstd_compress

logger = getLogger(__name__)


class Retriever(object):
    def retrieve_revisions(self, limit: Optional[int] = None) -> None:
        phabricator.set_api_key(
            get_secret("PHABRICATOR_URL"), get_secret("PHABRICATOR_TOKEN")
        )

        db.download(phabricator.REVISIONS_DB)

        # Get the commits DB, as we need it to get the revision IDs linked to recent commits.
        assert db.download(repository.COMMITS_DB)

        # Get the bugs DB, as we need it to get the revision IDs linked to bugs.
        assert db.download(bugzilla.BUGS_DB)

        phabricator.download_modified_revisions()

        # Get IDs of revisions linked to commits since a year ago.
        start_date = datetime.now(timezone.utc) - relativedelta(years=1)
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
        if limit is not None:
            revision_ids = revision_ids[-limit:]

        # Get IDs of revisions linked to bugs since a year ago.
        for bug in bugzilla.get_bugs():
            if dateutil.parser.parse(bug["last_change_time"]) < start_date:
                continue

            revision_ids += bugzilla.get_revision_ids(bug)

        phabricator.download_revisions(revision_ids)

        zstd_compress(phabricator.REVISIONS_DB)


def main() -> None:
    description = "Retrieve revisions from Phabricator"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--limit",
        type=int,
        help="Only download the N oldest revisions, used mainly for integration tests",
    )

    # Parse args to show the help if `--help` is passed
    args = parser.parse_args()

    retriever = Retriever()
    retriever.retrieve_revisions(args.limit)


if __name__ == "__main__":
    main()
