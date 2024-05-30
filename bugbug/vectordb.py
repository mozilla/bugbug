# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from itertools import islice
from typing import Iterable

from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PointStruct, VectorParams
from unidiff import Hunk

from bugbug.tools.code_review import InlineComment


# TODO: Replace this with `from itertools import batched` once we move to
# Python 3.12. The function below was copied from Itertools recipes.
# Source: https://docs.python.org/3.11/library/itertools.html#itertools-recipes
def batched(iterable, n):
    """Batch data into tuples of length n. The last batch may be shorter."""
    # batched('ABCDEFG', 3) --> ABC DEF G
    if n < 1:
        raise ValueError("n must be at least one")
    it = iter(iterable)
    while batch := tuple(islice(it, n)):
        yield batch


@dataclass
class VectorPoint:
    id: int
    vector: list[float]
    payload: dict


class VectorDB(ABC):
    """Abstract class for a vector database.

    You can implement this class to support different vector databases.
    """

    @abstractmethod
    def setup(self):
        ...

    @abstractmethod
    def upsert(self, points: Iterable[VectorPoint]):
        ...

    @abstractmethod
    def search(self, query: list[float]):
        ...


class QdrantVectorDB(VectorDB):
    def __init__(
        self, location: str, api_key: str, collection_name: str, *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.collection_name = collection_name
        self.client = QdrantClient(location=location, api_key=api_key)

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

    def upsert(self, points: Iterable[VectorPoint]):
        self.client.upsert(
            self.collection_name,
            [
                PointStruct(
                    id=point.id,
                    vector=point.vector,
                    payload=point.payload,
                )
                for point in points
            ],
        )

    def search(self, query: list[float]):
        return self.client.search(self.collection_name, query)


class ReviewCommentsDB:
    NAV_PATTERN = re.compile(r"\{nav, [^}]+\}")
    WHITESPACE_PATTERN = re.compile(r"[\n\s]+")

    def __init__(self, vector_db: VectorDB) -> None:
        self.vector_db = vector_db
        self.embeddings = OpenAIEmbeddings(model="text-embedding-3-large")

    def clean_comment(self, comment):
        # TODO: use the nav info instead of removing it
        comment = self.NAV_PATTERN.sub("", comment)
        comment = self.WHITESPACE_PATTERN.sub(" ", comment)
        comment = comment.strip()

        return comment

    def add_comments_by_hunk(self, items: Iterable[tuple[Hunk, InlineComment]]):
        for batch in batched(items, 100):
            self.vector_db.upsert(
                VectorPoint(
                    id=comment.id,
                    vector=self.embeddings.embed_query(str(hunk)),
                    payload=asdict(comment),
                )
                for comment, hunk in batch
            )

    def find_similar_hunk_comments(self, hunk: Hunk):
        return self.vector_db.search(self.embeddings.embed_query(str(hunk)))
