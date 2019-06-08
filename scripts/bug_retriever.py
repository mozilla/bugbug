# -*- coding: utf-8 -*-

import argparse
import lzma
import shutil
from datetime import datetime
from logging import INFO, basicConfig, getLogger

from dateutil.relativedelta import relativedelta

from bugbug import bug_snapshot, bugzilla, db, labels
from bugbug.utils import get_secret

basicConfig(level=INFO)
logger = getLogger(__name__)


class Retriever(object):
    def retrieve_bugs(self):
        bugzilla.set_token(get_secret("BUGZILLA_TOKEN"))

        db.download_version(bugzilla.BUGS_DB)
        if not db.is_old_version(bugzilla.BUGS_DB):
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
        logger.info(f"Retrieved {len(timespan_ids)} IDs.")

        # Get IDs of labelled bugs.
        labelled_bug_ids = labels.get_all_bug_ids()
        logger.info(f"{len(labelled_bug_ids)} labelled bugs to download.")

        all_ids = set(timespan_ids + labelled_bug_ids)

        # We have to redownload bugs that were changed since the last download.
        # We can remove from the DB the bugs that are outside of the considered timespan and are not labelled.
        bugzilla.delete_bugs(
            lambda bug: bug["id"] in changed_ids or bug["id"] not in all_ids
        )

        bugzilla.download_bugs(timespan_ids + labelled_bug_ids)

        # Try to re-download inconsistent bugs, up to three times.
        for i in range(3):
            bug_ids = set(bug_snapshot.get_inconsistencies())
            if len(bug_ids) == 0:
                break

            logger.info(
                f"Re-downloading {len(bug_ids)} bugs, as they were inconsistent"
            )
            bugzilla.delete_bugs(lambda bug: bug["id"] in bug_ids)
            bugzilla.download_bugs(bug_ids)

        self.compress_file("data/bugs.json")

    def compress_file(self, path):
        with open(path, "rb") as input_f:
            with lzma.open(f"{path}.xz", "wb") as output_f:
                shutil.copyfileobj(input_f, output_f)


def main():
    description = "Retrieve and extract the information from Bugzilla instance"
    parser = argparse.ArgumentParser(description=description)

    # Parse args to show the help if `--help` is passed
    parser.parse_args()

    retriever = Retriever()
    retriever.retrieve_bugs()
