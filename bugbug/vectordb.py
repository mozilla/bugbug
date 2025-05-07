# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, Optional

from qdrant_client import QdrantClient
from qdrant_client.conversions import common_types as qdrant_types
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


@dataclass
class QueryFilter:
    """A filter for a vector DB query.

    Attributes:
        must_match: The key is the field name and the value what must be matched.
        must_not_has_id: A list of IDs to exclude from the search results.
        must_range: A dictionary of field names and their ranges. The key is the
            field name, and the value is a dictionary with range values. See
            https://qdrant.tech/documentation/concepts/filtering/#range for more
            details.
    """

    must_match: Optional[dict[str, str | int]] = None
    must_not_has_id: Optional[list[int]] = None
    must_range: Optional[dict[str, dict[str, float]]] = None

    def to_qdrant_filter(self) -> qdrant_types.Filter:
        qdrant_filter: qdrant_types.Filter = {}

        if self.must_match:
            qdrant_filter["must"] = [
                {"key": key, "match": {"value": value}}
                for key, value in self.must_match.items()
            ]

        if self.must_range:
            if "must" not in qdrant_filter:
                qdrant_filter["must"] = []

            for key, value in self.must_range.items():
                qdrant_filter["must"].append(
                    {
                        "key": key,
                        "range": value,
                    }
                )

        if self.must_not_has_id:
            qdrant_filter["must_not"] = [{"has_id": self.must_not_has_id}]

        return qdrant_filter or None


class VectorDB(ABC):
    """Abstract class for a vector database.

    You can implement this class to support different vector databases.
    """

    @abstractmethod
    def setup(self): ...

    @abstractmethod
    def insert(self, points: Iterable[VectorPoint]): ...

    @abstractmethod
    def search(
        self, query: list[float], filter: QueryFilter | None = None, limit: int = 10
    ) -> Iterable[PayloadScore]: ...

    @abstractmethod
    def get_largest_id(self) -> int: ...

    @abstractmethod
    def get_existing_ids(self) -> Iterable[int]: ...


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

    def search(
        self, query: list[float], filter: QueryFilter | None = None, limit: int = 10
    ) -> Iterable[PayloadScore]:
        qdrant_filter = filter.to_qdrant_filter() if filter else None

        for item in self.client.search(
            self.collection_name, query, qdrant_filter, limit=limit
        ):
            yield PayloadScore(item.score, item.id, item.payload)

    def get_existing_ids(self) -> Iterable[int]:
        offset = 0

        while offset is not None:
            points, offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=100000,
                with_payload=False,
                with_vectors=False,
                offset=offset,
            )

            for point in points:
                yield point.id

    def get_largest_id(self) -> int:
        offset = 0
        while offset is not None:
            points, offset = self.client.scroll(
                collection_name=self.collection_name,
                with_payload=False,
                with_vectors=False,
                offset=offset,
            )

        return points[-1].id if points else 0
