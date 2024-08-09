# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PointStruct, VectorParams

from bugbug.utils import get_secret


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


class QdrantVectorDB(VectorDB):
    def __init__(self, collection_name: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.collection_name = collection_name
        self.client = QdrantClient(
            location=get_secret("QDRANT_LOCATION"), api_key=get_secret("QDRANT_API_KEY")
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
