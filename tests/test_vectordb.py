# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.vectordb import QdrantVectorDB, QueryFilter, VectorPoint


def test_qdrant_search_with_and_without_filters(monkeypatch):
    monkeypatch.setenv("QDRANT_LOCATION", ":memory:")
    monkeypatch.delenv("QDRANT_API_KEY", raising=False)
    db = QdrantVectorDB("test_search")
    db.setup()

    def vector(x, y):
        return [x, y] + [0.0] * 3070

    db.insert(
        [
            VectorPoint(1, vector(1.0, 0.0), {"kind": "keep", "rank": 1}),
            VectorPoint(2, vector(0.8, 0.6), {"kind": "keep", "rank": 2}),
            VectorPoint(3, vector(0.0, 1.0), {"kind": "skip", "rank": 3}),
        ]
    )

    assert [point.id for point in db.search(vector(1.0, 0.0), limit=2)] == [1, 2]

    results = list(
        db.search(
            vector(1.0, 0.0),
            filter=QueryFilter(
                must_match={"kind": "keep"},
                must_not_has_id=[2],
                must_range={"rank": {"gte": 1.0}},
            ),
        )
    )
    assert [(point.id, point.payload) for point in results] == [
        (1, {"kind": "keep", "rank": 1})
    ]
