# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import numpy as np
import pytest
from gensim.models import KeyedVectors

from bugbug import nlp


def build_embeddings():
    model = KeyedVectors(vector_size=3)
    model.add_vectors(
        ["if", "hello", "world", "alpha", "beta"],
        np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 2.0, 3.0],
                [3.0, 4.0, 5.0],
                [1.0, 2.0, 0.0],
                [3.0, 5.0, 0.0],
            ],
            dtype=np.float32,
        ),
    )
    return model


def test_spacy_token_lemmatizer_truncates_input():
    oversized_text = "a" * (nlp.nlp.max_length + 10)

    with pytest.raises(ValueError):
        nlp.nlp(oversized_text)

    lemmas = nlp.spacy_token_lemmatizer(oversized_text)
    assert len(lemmas) >= 1


def test_lemmatizing_tfidf_vectorizer_end_to_end():
    vectorizer = nlp.lemmatizing_tfidf_vectorizer(min_df=1)
    transformed = vectorizer.fit_transform(["hello world", "hello there"])

    assert vectorizer.tokenizer is nlp.spacy_token_lemmatizer
    assert transformed.shape[0] == 2
    assert transformed.shape[1] == 3
    assert not np.allclose(transformed.toarray(), 0.0)


def test_mean_embedding_transformer_fit_transform_and_feature_names(monkeypatch):
    monkeypatch.setattr(nlp, "get_word_embeddings", build_embeddings)

    transformer = nlp.MeanEmbeddingTransformer()

    assert transformer.fit(["ignored"]) is transformer

    transformed = transformer.transform(["hello world", "HELLO missing", "missing"])

    assert transformed.shape == (3, 3)
    np.testing.assert_allclose(transformed[0], np.array([2.0, 3.0, 4.0]))
    np.testing.assert_allclose(transformed[1], np.array([1.0, 2.0, 3.0]))
    np.testing.assert_allclose(transformed[2], np.zeros(3))

    feature_names = transformer.get_feature_names()
    np.testing.assert_array_equal(
        feature_names, np.array(["_0", "_1", "_2"], dtype=object)
    )


def test_tfidf_mean_embedding_transformer_fit_and_transform(monkeypatch):
    monkeypatch.setattr(nlp, "get_word_embeddings", build_embeddings)

    transformer = nlp.TfidfMeanEmbeddingTransformer()

    train_docs = [["alpha", "beta"], ["alpha"], ["beta"]]
    transformer.fit(train_docs)

    assert transformer.word2weight["alpha"] == transformer.word2weight["beta"]
    assert transformer.word2weight["unknown"] >= transformer.word2weight["alpha"]

    transformed = transformer.transform(["alpha beta", "unknown", "ALPHA"])

    alpha_weight = transformer.word2weight["alpha"]
    beta_weight = transformer.word2weight["beta"]
    expected_first = np.mean(
        [
            transformer.model["alpha"] * alpha_weight,
            transformer.model["beta"] * beta_weight,
        ],
        axis=0,
    )

    np.testing.assert_allclose(transformed[0], expected_first)
    np.testing.assert_allclose(transformed[1], np.zeros(3))
    np.testing.assert_allclose(
        transformed[2], transformer.model["alpha"] * alpha_weight
    )
