import argparse
import logging
import os
import re

import orjson
from libmozdata.phabricator import PhabricatorAPI

from bugbug import db, phabricator
from bugbug.phabricator import fetch_diff_from_url
from bugbug.tools.code_review import PhabricatorReviewData
from bugbug.utils import (
    get_secret,
    setup_libmozdata,
    zstd_compress,
)

review_data = PhabricatorReviewData()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

setup_libmozdata()
api = PhabricatorAPI(get_secret("PHABRICATOR_TOKEN"))


class NoDiffsFoundException(Exception):
    def __init__(self, patch_id):
        super().__init__(f"No diffs found for the given patch ID: {patch_id}")
        self.patch_id = patch_id


class NoTransactionsFoundException(Exception):
    def __init__(self, patch_id):
        super().__init__(f"No transactions found for the given patch ID: {patch_id}")
        self.patch_id = patch_id


class NoDiffFoundForPHIDException(Exception):
    def __init__(self, phid):
        super().__init__(f"No diff found for PHID {phid}")
        self.phid = phid


def load_revisions_maps():
    diff_id_to_revision = {}
    diff_phid_to_id = {}

    for revision in phabricator.get_revisions():
        for transaction in revision["transactions"]:
            if transaction.get("fields", {}).get("diff") is None:
                continue

            diff_id_to_revision[transaction["fields"]["diff"]["id"]] = revision
            diff_phid_to_id[transaction["fields"]["diff"]["phid"]] = transaction[
                "fields"
            ]["diff"]["id"]

    return diff_id_to_revision, diff_phid_to_id


def find_recent_update(transactions, comment_date_modified):
    updates = [
        transaction
        for transaction in transactions
        if transaction["type"] == "update"
        and transaction["dateModified"] <= comment_date_modified
    ]
    return max(
        updates, key=lambda transaction: transaction["dateModified"], default=None
    )


def extract_relevant_diff(patch_diff, filename):
    file_diff_pattern = rf"diff --git a/{re.escape(filename)} b/{re.escape(filename)}\n.*?(?=\ndiff --git|$)"
    match = re.search(file_diff_pattern, patch_diff, re.DOTALL)

    if match:
        return match.group(0)
    else:
        return None


def process_comments(limit, diff_length_limit):
    patch_count = 0
    diff_id_to_revisions_map, diff_phid_to_id = load_revisions_maps()

    for patch_id, comments in review_data.get_all_inline_comments(lambda c: True):
        revision_info = diff_id_to_revisions_map[patch_id]
        transactions = revision_info["transactions"]

        resolved_comments = [comment for comment in comments if comment.is_done]

        if not resolved_comments:
            continue

        for comment in comments:
            comment_date_modified = comment.date_modified
            most_recent_update = find_recent_update(transactions, comment_date_modified)
            if not most_recent_update:
                continue

            try:
                fix_patch_id = diff_phid_to_id[most_recent_update["fields"]["new"]]
            except KeyError:
                diffs = api.search_diffs(diff_phid=most_recent_update["fields"]["new"])
                if not diffs:
                    raise NoDiffFoundForPHIDException(
                        most_recent_update["fields"]["new"]
                    )
                fix_patch_id = diffs[0]["id"]

            # If the most recent patch is the original patch itself, skip it
            if fix_patch_id == patch_id:
                continue

            revision_phid = revision_info["phid"]
            revision_id = revision_info["id"]
            bug_id = revision_info["fields"]["bugzilla.bug-id"]

            try:
                previous_patch_id = diff_phid_to_id[most_recent_update["fields"]["old"]]
            except Exception:
                diffs = api.search_diffs(diff_phid=most_recent_update["fields"]["old"])
                if not diffs:
                    raise NoDiffFoundForPHIDException(
                        most_recent_update["fields"]["old"]
                    )
                previous_patch_id = diffs[0]["id"]

            try:
                patch_diff = fetch_diff_from_url(
                    revision_id, previous_patch_id, fix_patch_id
                )
            except Exception as e:
                logger.error(f"Failed to fetch diff: {e}")
                continue

            if len(patch_diff) > diff_length_limit:
                continue

            relevant_diff = extract_relevant_diff(patch_diff, comment.filename)

            if relevant_diff:
                data = {
                    "bug_id": bug_id,
                    "revision_id": revision_id,
                    "revision_phid": revision_phid,
                    "initial_patch_id": patch_id,
                    "fix_patch_id": fix_patch_id,
                    "previous_patch_id": previous_patch_id,
                    "comment": comment.__dict__,
                    "fix_patch_diff": relevant_diff,
                }
                yield data

        patch_count += 1
        if patch_count >= limit:
            break


def main():
    parser = argparse.ArgumentParser(description="Process patch reviews.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of patches to process. No limit if not specified.",
    )
    parser.add_argument(
        "--diff-length-limit",
        type=int,
        default=10000,
        help="Limit the maximum allowed diff length. Default 10000 if not specified.",
    )

    args = parser.parse_args()

    limit = args.limit or float("inf")
    diff_length_limit = args.diff_length_limit or float("inf")

    os.makedirs("patches", exist_ok=True)

    db.download(phabricator.REVISIONS_DB)

    with open(phabricator.FIXED_COMMENTS_DB, "wb") as dataset_file_handle:
        for data in process_comments(
            limit=limit,
            diff_length_limit=diff_length_limit,
        ):
            dataset_file_handle.write(orjson.dumps(data) + b"\n")

    zstd_compress(phabricator.FIXED_COMMENTS_DB)


if __name__ == "__main__":
    main()
