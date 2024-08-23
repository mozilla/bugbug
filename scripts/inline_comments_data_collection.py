import json
import logging
import os

from libmozdata.phabricator import PhabricatorAPI

from bugbug.tools.code_review import PhabricatorReviewData
from bugbug.utils import get_secret

review_data = PhabricatorReviewData()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.makedirs("patches", exist_ok=True)
os.makedirs("dataset", exist_ok=True)

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


def find_revision_from_patch(patch_id):
    diffs = api.search_diffs(diff_id=patch_id)

    if not diffs:
        raise NoDiffsFoundException(patch_id)

    revision_phid = diffs[0]["revisionPHID"]
    return revision_phid


def find_transactions_from_patch(patch_id):
    revision_phid = find_revision_from_patch(patch_id)
    transactions = api.request("transaction.search", objectIdentifier=revision_phid)[
        "data"
    ]

    if not transactions:
        raise NoTransactionsFoundException(patch_id)

    return transactions


def get_diff_info_from_phid(phid):
    diffs = api.search_diffs(diff_phid=phid)
    if not diffs:
        raise NoDiffFoundForPHIDException(phid)
    return diffs[0]["id"], diffs[0]["revisionPHID"]


def find_bugid_from_revision_phid(phid):
    revision = api.load_revision(rev_phid=phid)
    return revision["fields"]["bugzilla.bug-id"]


def find_recent_update(transactions, comment_date_modified):
    updates = [
        transaction
        for transaction in transactions
        if transaction["type"] == "update"
        and transaction["dateModified"] <= comment_date_modified
    ]
    if not updates:
        return None
    most_recent_update = max(
        updates, key=lambda transaction: transaction["dateModified"]
    )
    return most_recent_update


def to_int(value):
    if not value:
        return None
    if not isinstance(value, int):
        return int(value)
    return value


def process_comments(patch_threshold, diff_length_threshold):
    patch_count = 0

    for patch_id, comments in review_data.get_all_inline_comments(lambda c: True):
        transactions = find_transactions_from_patch(patch_id)

        resolved_comments = [comment for comment in comments if comment.is_done]

        if not resolved_comments:
            continue

        for comment in comments:
            comment_date_modified = comment.date_modified
            most_recent_update = find_recent_update(transactions, comment_date_modified)
            if not most_recent_update:
                continue

            fix_patch_id, revision_phid = get_diff_info_from_phid(
                most_recent_update["fields"]["new"]
            )

            # If the most recent patch is the original patch itself, skip it
            if fix_patch_id == patch_id:
                continue

            bug_id = find_bugid_from_revision_phid(phid=revision_phid)
            review_data.load_patch_by_id(fix_patch_id)

            with open(f"patches/{fix_patch_id}.patch", "r") as f:
                patch_diff = f.read()

            if len(patch_diff) > diff_length_threshold:
                continue

            data = {
                "bug_id": to_int(bug_id),
                "revision_phid": revision_phid,
                "initial_patch_id": to_int(patch_id),
                "fix_patch_id": to_int(fix_patch_id),
                "comment": comment.__dict__,
                "fix_patch_diff": patch_diff,
            }
            yield data

        patch_count += 1
        if patch_count >= patch_threshold:
            break


def main():
    dataset_file_path = "dataset/inline_comment_dataset.json"
    with open(dataset_file_path, "a") as dataset_file_handle:
        for data in process_comments(patch_threshold=250, diff_length_threshold=5000):
            dataset_file_handle.write(json.dumps(data) + "\n")


if __name__ == "__main__":
    main()
