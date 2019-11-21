# -*- coding: utf-8 -*-

import argparse
from datetime import datetime
from logging import getLogger

import dateutil.parser
from dateutil.relativedelta import relativedelta

from bugbug import bug_snapshot, bugzilla, db, labels, repository
from bugbug.utils import get_secret, zstd_compress

logger = getLogger(__name__)


class Retriever(object):
    def retrieve_bugs(self, limit=None):
        bugzilla.set_token(get_secret("BUGZILLA_TOKEN"))

        db.download(bugzilla.BUGS_DB)

        # Get IDs of bugs changed since last run.
        last_modified = db.last_modified(bugzilla.BUGS_DB)
        logger.info(
            f"Retrieving IDs of bugs modified since the last run on {last_modified}"
        )
        changed_ids = bugzilla.get_ids(
            {"f1": "delta_ts", "o1": "greaterthaneq", "v1": last_modified.date()}
        )
        logger.info(f"Retrieved {len(changed_ids)} IDs.")

        # Get IDs of bugs between (two years and six months ago) and (six months ago).
        six_months_ago = datetime.utcnow() - relativedelta(months=6)
        two_years_and_six_months_ago = six_months_ago - relativedelta(years=2)
        logger.info(
            f"Retrieving bug IDs from {two_years_and_six_months_ago} to {six_months_ago}"
        )
        timespan_ids = bugzilla.get_ids_between(
            two_years_and_six_months_ago, six_months_ago
        )
        if limit:
            timespan_ids = timespan_ids[:limit]
        logger.info(f"Retrieved {len(timespan_ids)} IDs.")

        # Get IDs of labelled bugs.
        labelled_bug_ids = labels.get_all_bug_ids()
        if limit:
            labelled_bug_ids = labelled_bug_ids[:limit]
        logger.info(f"{len(labelled_bug_ids)} labelled bugs to download.")

        # Get the commits DB, as we need it to get the bug IDs linked to recent commits.
        # XXX: Temporarily avoid downloading the commits DB when a limit is set, to avoid the integration test fail when the commits DB is bumped.
        if limit is None:
            assert db.download(repository.COMMITS_DB)

        # Get IDs of bugs linked to commits (used for some commit-based models, e.g. backout and regressor).
        start_date = datetime.now() - relativedelta(years=2, months=6)
        commit_bug_ids = [
            commit["bug_id"]
            for commit in repository.get_commits()
            if commit["bug_id"]
            and dateutil.parser.parse(commit["pushdate"]) >= start_date
        ]
        if limit:
            commit_bug_ids = commit_bug_ids[-limit:]
        logger.info(f"{len(commit_bug_ids)} bugs linked to commits to download.")

        # Get IDs of bugs which caused regressions fixed by commits (useful for the regressor model).
        regressed_by_bug_ids = sum(
            [
                bug["regressed_by"]
                for bug in bugzilla.get_bugs()
                if bug["id"] in commit_bug_ids
            ],
            [],
        )
        if limit:
            regressed_by_bug_ids = regressed_by_bug_ids[-limit:]
        logger.info(
            f"{len(regressed_by_bug_ids)} bugs which caused regressions fixed by commits."
        )

        all_ids = (
            timespan_ids + labelled_bug_ids + commit_bug_ids + regressed_by_bug_ids
        )
        all_ids_set = set(all_ids)

        # We have to redownload bugs that were changed since the last download.
        # We can remove from the DB the bugs that are outside of the considered timespan and are not labelled.
        bugzilla.delete_bugs(
            lambda bug: bug["id"] in changed_ids or bug["id"] not in all_ids_set
        )

        bugzilla.download_bugs(all_ids)

        # Get regressed_by_bug_ids again (the set could have changed after downloading new bugs).
        regressed_by_bug_ids = sum(
            [
                bug["regressed_by"]
                for bug in bugzilla.get_bugs()
                if bug["id"] in commit_bug_ids
            ],
            [],
        )
        logger.info(
            f"{len(regressed_by_bug_ids)} bugs which caused regressions fixed by commits."
        )

        bugzilla.download_bugs(regressed_by_bug_ids)

        # Try to re-download inconsistent bugs, up to three times.
        inconsistent_bugs = bugzilla.get_bugs()
        for i in range(3):
            # We look for inconsistencies in all bugs first, then, on following passes,
            # we only look for inconsistencies in bugs that were found to be inconsistent in the first pass
            inconsistent_bugs = bug_snapshot.get_inconsistencies(inconsistent_bugs)
            inconsistent_bug_ids = set(bug["id"] for bug in inconsistent_bugs)

            if len(inconsistent_bug_ids) == 0:
                break

            logger.info(
                f"Re-downloading {len(inconsistent_bug_ids)} bugs, as they were inconsistent"
            )
            bugzilla.delete_bugs(lambda bug: bug["id"] in inconsistent_bug_ids)
            bugzilla.download_bugs(inconsistent_bug_ids)

        zstd_compress("data/bugs.json")


def main():
    description = "Retrieve and extract the information from Bugzilla instance"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--limit",
        type=int,
        help="Only download the N oldest bugs, used mainly for integration tests",
    )

    # Parse args to show the help if `--help` is passed
    args = parser.parse_args()

    retriever = Retriever()
    retriever.retrieve_bugs(args.limit)


if __name__ == "__main__":
    main()
