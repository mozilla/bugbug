# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


from unidiff import PatchSet

from bugbug.tools.code_review import PhabricatorReviewData, ReviewCommentsDB
from bugbug.vectordb import QdrantVectorDB


def main():
    review_data = PhabricatorReviewData()
    vector_db = QdrantVectorDB("diff_comments")
    vector_db.setup()
    comments_db = ReviewCommentsDB(vector_db)
    # TODO: support resuming from where last run left off. We should run it from
    # scratch only once. Following runs should add only new comments.
    comments_db.add_comments_by_hunk(review_data.retrieve_comments_with_hunks())


def retrieve_examples(review_data, embedding, patch, num: int = 3):
    # TODO: add an option to switch type of VectorDB
    vector_db = QdrantVectorDB("diff_comments", embedding_size=embedding.size)
    vector_db.setup()
    comments_db = ReviewCommentsDB(vector_db, embedding)
    comments_db.add_comments_by_hunk(review_data.retrieve_comments_with_hunks())

    patch_set = PatchSet.from_string(patch.raw_diff)
    all_examples = {}

    for file in patch_set:
        for hunk in file:
            retrieved = comments_db.find_similar_hunk_comments(hunk)
            for ex in retrieved:
                if ex.id not in all_examples:
                    all_examples[ex.id] = {"score": 0, "payload": ex.payload}
                all_examples[ex.id]["score"] += ex.score
    list_all_examples = [
        (all_examples[e]["score"], all_examples[e]["payload"]) for e in all_examples
    ]
    list_all_examples.sort()

    return list_all_examples[-num:]


if __name__ == "__main__":
    main()
