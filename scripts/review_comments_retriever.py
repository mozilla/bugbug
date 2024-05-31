# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


import os
import re
from collections import defaultdict
from functools import lru_cache

import tenacity
from libmozdata.phabricator import PhabricatorAPI
from tqdm import tqdm
from unidiff import Hunk, PatchedFile, PatchSet, UnidiffParseError

from bugbug import db, phabricator
from bugbug.tools.code_review import InlineComment
from bugbug.utils import get_secret
from bugbug.vectordb import QdrantVectorDB, ReviewCommentsDB


class PhabricatorReviewCommentRetriever:
    NIT_PATTERN = re.compile(r"[^a-zA-Z0-9]nit[\s:,]", re.IGNORECASE)

    def __init__(self) -> None:
        os.makedirs("patches", exist_ok=True)
        self.phabricator_api = PhabricatorAPI(
            get_secret("PHABRICATOR_TOKEN"),
            get_secret("PHABRICATOR_URL"),
        )

        db.download(phabricator.REVISIONS_DB)

    @lru_cache(maxsize=1)
    @tenacity.retry(
        stop=tenacity.stop_after_attempt(7),
        wait=tenacity.wait_exponential(multiplier=1, min=16, max=64),
        reraise=True,
    )
    def load_patch_set(self, diff_id):
        try:
            with open(f"patches/{diff_id}.patch", "r") as f:
                patch = f.read()
        except FileNotFoundError:
            with open(f"patches/{diff_id}.patch", "w") as f:
                patch = self.phabricator_api.load_raw_diff(diff_id)
                f.write(patch)

        return PatchSet.from_string(patch)

    def _get_revisions_count(self):
        return sum(1 for _ in phabricator.get_revisions())

    def retrieve_diff_comments(self):
        for revision in tqdm(
            phabricator.get_revisions(), total=self._get_revisions_count()
        ):
            diff_comments: dict[int, list[InlineComment]] = defaultdict(list)

            for transaction in revision["transactions"]:
                if transaction["type"] != "inline":
                    continue

                # Ignore replies
                if transaction["fields"]["replyToCommentPHID"] is not None:
                    continue

                # Ignore bot comments
                if transaction["authorPHID"] == "PHID-USER-cje4weq32o3xyuegalpj":
                    continue

                if len(transaction["comments"]) != 1:
                    print(transaction)
                    # raise Exception("Unexpected, need to look into it")
                    continue

                comment_content = transaction["comments"][0]["content"]["raw"]

                # Ignore very short and very log comments
                if not 50 < len(comment_content) < 500:
                    continue

                # Ignore comments with URLs
                if "https://" in comment_content or "http://" in comment_content:
                    continue

                #  Ignore nit comments
                if self.NIT_PATTERN.search(comment_content):
                    continue

                # Ignore comments with code blocks
                if "```" in comment_content:
                    continue

                comment_lower = comment_content.lower()
                if any(
                    phrase in comment_lower
                    for phrase in [
                        "wdyt?",
                        "what do you think?",
                        "you explain",
                        "understand",
                    ]
                ):
                    continue

                diff_id = transaction["fields"]["diff"]["id"]
                comment_id = transaction["comments"][0]["id"]
                date_created = transaction["comments"][0]["dateCreated"]
                filename = transaction["fields"]["path"]
                start_line = transaction["fields"]["line"]
                end_line = (
                    transaction["fields"]["line"] + transaction["fields"]["length"] - 1
                )
                # Unfortunately, we do not have this information for a limitation
                # in Phabricator's API. We assume it as true as a workaround.
                on_added_code = True

                diff_comments[diff_id].append(
                    # TODO: we could create an extended dataclass for this
                    # instead of adding optional fields.
                    InlineComment(
                        filename,
                        start_line,
                        end_line,
                        comment_content,
                        on_added_code,
                        comment_id,
                        date_created,
                    )
                )

                # print(f"Processing {diff_id} {path} {line_number} {line_length}")

            for diff_id, comments in diff_comments.items():
                yield diff_id, comments

    def get_matching_hunk(self, patched_file: PatchedFile, start_line: int) -> Hunk:
        matching_hunks = [
            hunk
            for hunk in patched_file
            if hunk.target_start <= start_line < hunk.target_start + hunk.target_length
            or hunk.source_start <= start_line < hunk.source_start + hunk.source_length
        ]

        # If there is more than one matching hunk, choose the one where the
        # line number of the comment corresponds to an added or deleted line. We
        # prioritize added lines over deleted lines because comments are more
        # likely to be on added lines than deleted lines.
        if len(matching_hunks) > 1:
            for hunk in matching_hunks:
                for line in hunk:
                    if line.is_added and line.target_line_no == start_line:
                        return hunk

                for line in hunk:
                    if line.is_removed and line.source_line_no == start_line:
                        return hunk

        if len(matching_hunks) != 0:
            return matching_hunks[0]

    def retrieve_comments_with_hunks(self):
        for diff_id, comments in self.retrieve_diff_comments():
            try:
                patch_set = self.load_patch_set(diff_id)
            except UnidiffParseError:
                # TODO: use log instead of print
                print(f"Failed to parse {diff_id}")
                continue

            file_map = {
                patched_file.path: patched_file
                for patched_file in patch_set
                if patched_file.is_modified_file
            }
            for comment in comments:
                patched_file = file_map.get(comment.filename)
                if not patched_file:
                    continue

                hunk = self.get_matching_hunk(patched_file, comment.start_line)
                if not hunk:
                    continue

                yield comment, hunk


def main():
    retriever = PhabricatorReviewCommentRetriever()
    vector_db = QdrantVectorDB(
        get_secret("QDRANT_LOCATION"),
        get_secret("QDRANT_API_KEY"),
        "diff_comments",
    )
    vector_db.setup()
    comments_db = ReviewCommentsDB(vector_db)
    # TODO: support resuming from where last run left off. We should run it from
    # scratch only ones. Following runs should add only new comments.
    comments_db.add_comments_by_hunk(retriever.retrieve_comments_with_hunks())


if __name__ == "__main__":
    main()
