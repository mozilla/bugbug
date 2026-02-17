# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import sys
from collections import defaultdict
from logging import INFO, basicConfig, getLogger

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.feature_extraction.text import TfidfVectorizer

basicConfig(level=INFO)
logger = getLogger(__name__)

HAS_OPTIONAL_DEPENDENCIES = False

try:
    import spacy

    HAS_OPTIONAL_DEPENDENCIES = True
except ImportError:
    pass

try:
    if HAS_OPTIONAL_DEPENDENCIES:
        nlp = spacy.load("en_core_web_md")
except OSError:
    HAS_OPTIONAL_DEPENDENCIES = False
    logger.error(
        "Spacy model is missing, install it with: %s -m spacy download en_core_web_md",
        sys.executable,
    )

OPT_MSG_MISSING = (
    "Optional dependencies are missing, install them with: pip install bugbug[nlp]\n"
    "You might need also to download the models with: "
    f"{sys.executable} -m spacy download en_core_web_md"
)


def spacy_token_lemmatizer(text):
    if len(text) > nlp.max_length:
        text = text[: nlp.max_length - 1]
    doc = nlp(text)
    return [token.lemma_ for token in doc]


def lemmatizing_tfidf_vectorizer(**kwargs):
    # Detect when the Spacy optional dependency is missing.
    if not HAS_OPTIONAL_DEPENDENCIES:
        raise NotImplementedError(OPT_MSG_MISSING)

    return TfidfVectorizer(tokenizer=spacy_token_lemmatizer, **kwargs)


def _get_vector_dim():
    if nlp.vocab.vectors_length:
        return nlp.vocab.vectors_length

    doc = nlp("vector")
    if doc:
        return doc[0].vector.shape[0]

    return 0


def _token_vector(token):
    key = token.lower_

    # Check if there is a lowercase word vector first.
    if nlp.vocab.has_vector(key):
        return nlp.vocab.get_vector(key)

    if token.has_vector:
        return token.vector

    return None


class MeanEmbeddingTransformer(BaseEstimator, TransformerMixin):
    def __init__(self):
        # Detect when the Spacy optional dependency is missing.
        if not HAS_OPTIONAL_DEPENDENCIES:
            raise NotImplementedError(OPT_MSG_MISSING)

        self.dim = _get_vector_dim()

    def fit(self, x, y=None):
        return self

    def transform(self, data):
        return np.array(
            [
                np.mean(
                    [vec for token in doc if (vec := _token_vector(token)) is not None]
                    or [np.zeros(self.dim)],
                    axis=0,
                )
                for doc in nlp.pipe(data)
            ]
        )

    def get_feature_names(self):
        return np.array([f"_{i}" for i in range(self.dim)], dtype=object)


class TfidfMeanEmbeddingTransformer(MeanEmbeddingTransformer):
    def __init__(self):
        super().__init__()
        self.word2weight = None

    def fit(self, X, y=None):
        tfidf = TfidfVectorizer(analyzer=lambda x: x)
        tfidf.fit(X)

        # If a word was never seen, it must be at least as infrequent as any of the known words.
        # So, the default idf is the max of known idfs.
        max_idf = max(tfidf.idf_)
        self.word2weight = defaultdict(
            lambda: max_idf, [(w, tfidf.idf_[i]) for w, i in tfidf.vocabulary_.items()]
        )

        return self

    def transform(self, data):
        return np.array(
            [
                np.mean(
                    [
                        vec * self.word2weight[token.lower_]
                        for token in doc
                        if (vec := _token_vector(token)) is not None
                    ]
                    or [np.zeros(self.dim)],
                    axis=0,
                )
                for doc in nlp.pipe(data)
            ]
        )
