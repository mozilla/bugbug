# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import sys
from collections import defaultdict
from functools import lru_cache

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.feature_extraction.text import TfidfVectorizer

HAS_OPTIONAL_DEPENDENCIES = False

try:
    import spacy
    from gensim.models import KeyedVectors
    from spacy.tokenizer import Tokenizer

    HAS_OPTIONAL_DEPENDENCIES = True
except ImportError:
    pass

try:
    if HAS_OPTIONAL_DEPENDENCIES:
        nlp = spacy.load("en_core_web_sm")
except OSError:
    msg = (
        "Spacy model is missing, install it with: "
        f"{sys.executable} -m spacy download en_core_web_sm"
    )
    print(msg, file=sys.stderr)

OPT_MSG_MISSING = (
    "Optional dependencies are missing, install them with: pip install bugbug[nlp]\n"
    "You might need also to download the models with: "
    f"{sys.executable} -m spacy download en_core_web_sm"
)


def spacy_token_lemmatizer(text):
    if len(text) > nlp.max_length:
        text = text[: nlp.max_length - 1]
    doc = nlp(text)
    return [token.lemma_ for token in doc]


class SpacyVectorizer(TfidfVectorizer):
    def __init__(self, *args, **kwargs):

        # Detect when the Spacy optional dependency is missing
        if not HAS_OPTIONAL_DEPENDENCIES:
            raise NotImplementedError(OPT_MSG_MISSING)

        super().__init__(tokenizer=spacy_token_lemmatizer, *args, **kwargs)


@lru_cache()
def get_word_embeddings():
    word_embeddings = KeyedVectors.load_word2vec_format("wiki-news-300d-1M-subword.vec")
    word_embeddings.init_sims(replace=True)
    return word_embeddings


class MeanEmbeddingTransformer(BaseEstimator, TransformerMixin):
    def __init__(self):
        # Detect when the Gensim optional dependency are missing
        if not HAS_OPTIONAL_DEPENDENCIES:
            raise NotImplementedError(OPT_MSG_MISSING)

        self.model = get_word_embeddings()
        self.dim = len(self.model["if"])

    def fit(self, x, y=None):
        return self

    def transform(self, data):
        tokenizer = Tokenizer(nlp.vocab)
        return np.array(
            [
                np.mean(
                    [
                        self.model[w.text.lower()]
                        for w in words
                        if w.text.lower() in self.model
                    ]
                    or [np.zeros(self.dim)],
                    axis=0,
                )
                for words in tokenizer.pipe(data)
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
        tokenizer = Tokenizer(nlp.vocab)
        return np.array(
            [
                np.mean(
                    [
                        self.model[w.text.lower()] * self.word2weight[w.text.lower()]
                        for w in words
                        if w.text.lower() in self.model
                    ]
                    or [np.zeros(self.dim)],
                    axis=0,
                )
                for words in tokenizer.pipe(data)
            ]
        )
