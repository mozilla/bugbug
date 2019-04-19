# -*- coding: utf-8 -*-

import argparse
import lzma
import shutil
from datetime import datetime
from logging import INFO, basicConfig, getLogger

from dateutil.relativedelta import relativedelta

from bugbug import bug_snapshot, bugzilla, labels
from bugbug.utils import get_secret

basicConfig(level=INFO)
logger = getLogger(__name__)


class Retriever(object):
    def retrieve_bugs(self):
        bugzilla.set_token(get_secret("BUGZILLA_TOKEN"))

        six_months_ago = datetime.utcnow() - relativedelta(months=6)
        two_years_and_six_months_ago = six_months_ago - relativedelta(years=2)
        logger.info(
            "Downloading bugs from {} to {}".format(
                two_years_and_six_months_ago, six_months_ago
            )
        )
        bugzilla.download_bugs_between(two_years_and_six_months_ago, six_months_ago)

        logger.info("Downloading labelled bugs")
        bug_ids = labels.get_all_bug_ids()
        bugzilla.download_bugs(bug_ids)

        # Try to re-download inconsistent bugs, up to three times.
        for i in range(3):
            bug_ids = bug_snapshot.get_inconsistencies()
            if len(bug_ids) == 0:
                break

            logger.info(
                f"Re-downloading {len(bug_ids)} bugs, as they were inconsistent"
            )
            bugzilla.delete_bugs(bug_ids)
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
