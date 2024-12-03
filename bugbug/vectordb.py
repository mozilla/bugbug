# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PointStruct, VectorParams


@dataclass
class VectorPoint:
    id: int
    vector: list[float]
    payload: dict


@dataclass(order=True)
class PayloadScore:
    score: int
    id: int
    payload: dict


class VectorDB(ABC):
    """Abstract class for a vector database.

    You can implement this class to support different vector databases.
    """

    @abstractmethod
    def setup(self):
        ...

    @abstractmethod
    def insert(self, points: Iterable[VectorPoint]):
        ...

    @abstractmethod
    def search(self, query: list[float]) -> Iterable[PayloadScore]:
        ...

    @abstractmethod
    def get_largest_comment_id(self):
        ...

    @abstractmethod
    def update_most_recent_comment_id(self, largest_comment_id):
        ...

    @abstractmethod
    def get_most_recent_comment_id(self):
        ...

    @abstractmethod
    def delete_most_recent_comment_id(self):
        ...


class QdrantVectorDB(VectorDB):
    MOST_RECENT_COMMENT_ID = 9999999999

    def __init__(self, collection_name: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.collection_name = collection_name
        self.client = QdrantClient(
            location="http://localhost:6333"
            # location=get_secret("QDRANT_LOCATION"), api_key=get_secret("QDRANT_API_KEY")
        )

    def setup(self):
        config = VectorParams(
            size=3072,
            distance=Distance.COSINE,
        )
        try:
            self.client.create_collection(self.collection_name, vectors_config=config)
        except UnexpectedResponse as e:
            # We only allow 409 (collection already exists) to pass through
            if e.status_code != 409:
                raise Exception("Failed to create collection") from e

    def insert(self, points: Iterable[VectorPoint]):
        self.client.upload_points(
            self.collection_name,
            (
                PointStruct(
                    id=point.id,
                    vector=point.vector,
                    payload=point.payload,
                )
                for point in points
            ),
        )

    def search(self, query: list[float]) -> Iterable[PayloadScore]:
        for item in self.client.search(self.collection_name, query):
            yield PayloadScore(item.score, item.id, item.payload)

    def get_largest_comment_id(self):
        offset = None
        largest_id = 0

        while True:
            points, next_page_offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=100,
                with_payload=False,
                with_vectors=False,
                offset=offset,
            )

            if next_page_offset is None:
                largest_id = max(record.id for record in points)
                break
            else:
                offset = next_page_offset

        return largest_id

    def get_most_recent_comment_id(self):
        most_recent_comment_id = self.client.retrieve(
            collection_name=self.collection_name,
            ids=[self.MOST_RECENT_COMMENT_ID],
            with_payload=True,
        )
        if not most_recent_comment_id:
            return 0

        return most_recent_comment_id[0].payload["most_recent_comment_id"]

    def update_most_recent_comment_id(self, comment_id):
        most_recent_comment_id_point = VectorPoint(
            id=self.MOST_RECENT_COMMENT_ID,
            vector=[],
            payload={"most_recent_comment_id": comment_id},
        )
        self.insert([most_recent_comment_id_point])

    def delete_most_recent_comment_id(self):
        self.client.delete(
            self.collection_name,
            models.PointIdsList(points=[self.MOST_RECENT_COMMENT_ID]),
        )
