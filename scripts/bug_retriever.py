# -*- coding: utf-8 -*-

import argparse
from datetime import datetime
from logging import getLogger

import dateutil.parser
from dateutil.relativedelta import relativedelta

from bugbug import bug_snapshot, bugzilla, db, labels, repository, test_scheduling
from bugbug.utils import get_secret, zstd_compress

logger = getLogger(__name__)


class Retriever(object):
    def retrieve_bugs(self, limit: int | None = None) -> None:
        bugzilla.set_token(get_secret("BUGZILLA_TOKEN"))

        last_modified = None
        db.download(bugzilla.BUGS_DB)

        # Get IDs of bugs changed since last run.
        try:
            last_modified = db.last_modified(bugzilla.BUGS_DB)
        except db.LastModifiedNotAvailable:
            pass

        if last_modified is not None:
            logger.info(
                "Retrieving IDs of bugs modified since the last run on %s",
                last_modified,
            )
            changed_ids = set(
                bugzilla.get_ids(
                    {
                        "f1": "delta_ts",
                        "o1": "greaterthaneq",
                        "v1": last_modified.date(),
                    }
                )
            )
        else:
            changed_ids = set()

        logger.info("Retrieved %d IDs.", len(changed_ids))

        all_components = set(bugzilla.fetch_components_list())

        deleted_component_ids = set(
            bug["id"]
            for bug in bugzilla.get_bugs(
                include_invalid=True,
                include_additional_products=bugzilla.ADDITIONAL_PRODUCTS,
            )
            if (bug["product"], bug["component"]) not in all_components
        )
        logger.info(
            "%d bugs belonging to deleted components", len(deleted_component_ids)
        )
        changed_ids |= deleted_component_ids

        # Get IDs of bugs between (two years and six months ago) and now.
        two_years_and_six_months_ago = datetime.utcnow() - relativedelta(
            years=2, months=6
        )
        logger.info("Retrieving bug IDs since %s", two_years_and_six_months_ago)
        timespan_ids = bugzilla.get_ids_between(two_years_and_six_months_ago)
        if limit:
            timespan_ids = timespan_ids[-limit:]
        logger.info("Retrieved %d IDs.", len(timespan_ids))

        # Get IDs of labelled bugs.
        labelled_bug_ids = labels.get_all_bug_ids()
        if limit:
            labelled_bug_ids = labelled_bug_ids[-limit:]
        logger.info("%d labelled bugs to download.", len(labelled_bug_ids))

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
        logger.info("%d bugs linked to commits to download.", len(commit_bug_ids))

        # Get IDs of bugs which are regressions, bugs which caused regressions (useful for the regressor model),
        # and blocked bugs.
        regression_related_ids: list[int] = list(
            set(
                sum(
                    (
                        bug["regressed_by"] + bug["regressions"] + bug["blocks"]
                        for bug in bugzilla.get_bugs(
                            include_additional_products=bugzilla.ADDITIONAL_PRODUCTS
                        )
                    ),
                    [],
                )
            )
        )
        if limit:
            regression_related_ids = regression_related_ids[-limit:]
        logger.info(
            "%d bugs which caused regressions fixed by commits.",
            len(regression_related_ids),
        )

        # Get IDs of bugs linked to intermittent failures.
        test_failure_bug_ids = [
            item["bug_id"]
            for item in test_scheduling.get_failure_bugs(
                two_years_and_six_months_ago, datetime.utcnow()
            )
            if item["bug_id"] is not None
        ]
        if limit:
            test_failure_bug_ids = test_failure_bug_ids[-limit:]
        logger.info("%d bugs about test failures.", len(test_failure_bug_ids))

        all_ids = (
            timespan_ids
            + labelled_bug_ids
            + commit_bug_ids
            + regression_related_ids
            + test_failure_bug_ids
        )
        all_ids_set = set(all_ids)

        # We have to redownload bugs that were changed since the last download.
        # We can remove from the DB the bugs that are outside of the considered timespan and are not labelled.
        bugzilla.delete_bugs(
            lambda bug: bug["id"] in changed_ids or bug["id"] not in all_ids_set
        )

        new_bugs = bugzilla.download_bugs(all_ids)

        # Get regression_related_ids again (the set could have changed after downloading new bugs).
        for i in range(7):
            regression_related_ids = list(
                set(
                    sum(
                        (
                            bug["regressed_by"] + bug["regressions"] + bug["blocks"]
                            for bug in new_bugs
                        ),
                        [],
                    )
                )
            )
            logger.info(
                "%d bugs which caused regressions fixed by commits.",
                len(regression_related_ids),
            )
            if limit:
                regression_related_ids = regression_related_ids[-limit:]

            # If we got all bugs we needed, break.
            if set(regression_related_ids).issubset(all_ids):
                break

            new_bugs = bugzilla.download_bugs(regression_related_ids)

        # Try to re-download inconsistent bugs, up to twice.
        inconsistent_bugs = bugzilla.get_bugs(
            include_invalid=True,
            include_additional_products=bugzilla.ADDITIONAL_PRODUCTS,
        )
        for i in range(2):
            # We look for inconsistencies in all bugs first, then, on following passes,
            # we only look for inconsistencies in bugs that were found to be inconsistent in the first pass
            inconsistent_bugs = bug_snapshot.get_inconsistencies(inconsistent_bugs)
            inconsistent_bug_ids = set(bug["id"] for bug in inconsistent_bugs)

            if len(inconsistent_bug_ids) == 0:
                break

            logger.info(
                "Re-downloading %d bugs, as they were inconsistent",
                len(inconsistent_bug_ids),
            )
            bugzilla.delete_bugs(lambda bug: bug["id"] in inconsistent_bug_ids)
            bugzilla.download_bugs(inconsistent_bug_ids)

        # TODO: Figure out why we have missing fields in the first place.
        handle_missing_fields(["history", "comments"])

        zstd_compress(bugzilla.BUGS_DB)


def handle_missing_fields(
    fields: list[str], trial_number: int = 1, max_tries: int = 2
) -> int:
    """Handle bugs that are missing a mandatory field.

    This function will try to re-download the bugs that are missing any of the
    given fields and delete them if they are still missing a field after the
    maximum number of tries.

    Args:
        fields: The list of field names to check for.
        trial_number: The current try number. It should be 1 on the first call.
        max_tries: The maximum number of tries to re-download the bugs.

    Returns:
        Number of bugs that were deleted.
    """
    missing_field_bug_ids = {
        bug["id"]
        for bug in db.read(bugzilla.BUGS_DB)
        if any(field not in bug for field in fields)
    }

    if not missing_field_bug_ids:
        return 0

    bugzilla.delete_bugs(lambda bug: bug["id"] in missing_field_bug_ids)

    if trial_number <= max_tries:
        logger.info(
            "Re-downloading %d bugs, as they were missing the fields (re-trial %d of %d)",
            len(missing_field_bug_ids),
            trial_number,
            max_tries,
        )
        bugzilla.download_bugs(missing_field_bug_ids)

        return handle_missing_fields(fields, trial_number + 1, max_tries)

    logger.info(
        "Deleted %d bugs as we couldn't retrieve their missing fields",
        len(missing_field_bug_ids),
    )

    return len(missing_field_bug_ids)


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
