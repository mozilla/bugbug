# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


from bugbug.tools.code_review import PhabricatorReviewData, ReviewCommentsDB
from bugbug.vectordb import QdrantVectorDB


def main():
    review_data = PhabricatorReviewData()
    vector_db = QdrantVectorDB("diff_comments")
    vector_db.setup()
    comments_db = ReviewCommentsDB(vector_db)

    # True if we successfully fully populated the DB and now adding new datapoints
    # False if it failed while populating DB
    success_run = False

    # comments_db.delete_most_recent_comment_id()

    # TODO: support resuming from where last run left off. We should run it from
    # scratch only once. Following runs should add only new comments.
    comments_db.add_comments_by_hunk(
        review_data.retrieve_comments_with_hunks(), success_run
    )


if __name__ == "__main__":
    main()
