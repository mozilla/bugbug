# -*- coding: utf-8 -*-

import argparse
from datetime import datetime
from logging import getLogger
from typing import List

import dateutil.parser
from dateutil.relativedelta import relativedelta

from bugbug import bug_snapshot, bugzilla, db, labels, repository
from bugbug.utils import get_secret, zstd_compress

logger = getLogger(__name__)


class Retriever(object):
    def retrieve_bugs(self, limit=None) -> None:
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

        all_components = bugzilla.get_product_component_count(9999)

        deleted_component_ids = [
            bug["id"]
            for bug in bugzilla.get_bugs()
            if "{}::{}".format(bug["product"], bug["component"]) not in all_components
        ]
        logger.info(
            f"{len(deleted_component_ids)} bugs belonging to deleted components"
        )
        changed_ids += deleted_component_ids

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
            timespan_ids = timespan_ids[-limit:]
        logger.info(f"Retrieved {len(timespan_ids)} IDs.")

        # Get IDs of labelled bugs.
        labelled_bug_ids = labels.get_all_bug_ids()
        if limit:
            labelled_bug_ids = labelled_bug_ids[-limit:]
        logger.info(f"{len(labelled_bug_ids)} labelled bugs to download.")

        # Get the commits DB, as we need it to get the bug IDs linked to recent commits.
        # XXX: Temporarily avoid downloading the commits DB when a limit is set, to avoid the integration test fail when the commits DB is bumped.
        if limit is None:
            assert db.download(repository.COMMITS_DB)

        # Get IDs of bugs linked to commits (used for some commit-based models, e.g. backout and regressor).
        start_date = datetime.now() - relativedelta(years=3)
        commit_bug_ids = list(
            set(
                commit["bug_id"]
                for commit in repository.get_commits()
                if commit["bug_id"]
                and dateutil.parser.parse(commit["pushdate"]) >= start_date
            )
        )
        if limit:
            commit_bug_ids = commit_bug_ids[-limit:]
        logger.info(f"{len(commit_bug_ids)} bugs linked to commits to download.")

        # Get IDs of bugs which are regressions, bugs which caused regressions (useful for the regressor model),
        # and blocked bugs.
        regression_related_ids: List[int] = list(
            set(
                sum(
                    (
                        bug["regressed_by"] + bug["regressions"] + bug["blocks"]
                        for bug in bugzilla.get_bugs()
                    ),
                    [],
                )
            )
        )
        if limit:
            regression_related_ids = regression_related_ids[-limit:]
        logger.info(
            f"{len(regression_related_ids)} bugs which caused regressions fixed by commits."
        )

        all_ids = (
            timespan_ids + labelled_bug_ids + commit_bug_ids + regression_related_ids
        )
        all_ids_set = set(all_ids)

        # We have to redownload bugs that were changed since the last download.
        # We can remove from the DB the bugs that are outside of the considered timespan and are not labelled.
        bugzilla.delete_bugs(
            lambda bug: bug["id"] in changed_ids or bug["id"] not in all_ids_set
        )

        bugzilla.download_bugs(all_ids)

        # Get regression_related_ids again (the set could have changed after downloading new bugs).
        for i in range(3):
            regression_related_ids = list(
                set(
                    sum(
                        (
                            bug["regressed_by"] + bug["regressions"] + bug["blocks"]
                            for bug in bugzilla.get_bugs()
                        ),
                        [],
                    )
                )
            )
            logger.info(
                f"{len(regression_related_ids)} bugs which caused regressions fixed by commits."
            )
            if limit:
                regression_related_ids = regression_related_ids[-limit:]

            # If we got all bugs we needed, break.
            if set(regression_related_ids).issubset(all_ids):
                break

            bugzilla.download_bugs(regression_related_ids)

        # Try to re-download inconsistent bugs, up to twice.
        inconsistent_bugs = bugzilla.get_bugs(include_invalid=True)
        for i in range(2):
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

        # TODO: Figure out why.
        missing_history_bug_ids = {
            bug["id"] for bug in bugzilla.get_bugs() if "history" not in bug
        }
        bugzilla.delete_bugs(lambda bug: bug["id"] in missing_history_bug_ids)
        logger.info(
            f"Deleted {len(missing_history_bug_ids)} bugs as we couldn't retrieve their history"
        )

        zstd_compress(bugzilla.BUGS_DB)


def main() -> None:
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
