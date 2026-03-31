# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import numpy as np
import pytest
import spacy

from bugbug import nlp


@pytest.fixture()
def runtime_nlp(monkeypatch):
    model = spacy.blank("en")
    model.vocab.set_vector("hello", np.array([1.0, 2.0, 3.0], dtype=np.float32))
    model.vocab.set_vector("world", np.array([3.0, 4.0, 5.0], dtype=np.float32))
    model.vocab.set_vector("alpha", np.array([1.0, 2.0, 0.0], dtype=np.float32))
    model.vocab.set_vector("beta", np.array([3.0, 5.0, 0.0], dtype=np.float32))
    monkeypatch.setattr(nlp, "get_nlp", lambda: model)
    return model


def test_spacy_token_lemmatizer_truncates_input(runtime_nlp):
    oversized_text = "a" * (runtime_nlp.max_length + 10)

    with pytest.raises(ValueError):
        runtime_nlp(oversized_text)

    lemmas = nlp.spacy_token_lemmatizer(oversized_text)
    assert len(lemmas) >= 1


def test_lemmatizing_tfidf_vectorizer_end_to_end(runtime_nlp):
    docs = ["hello world", "hello there"]
    vectorizer = nlp.lemmatizing_tfidf_vectorizer(min_df=1)
    transformed = vectorizer.fit_transform(docs)

    assert vectorizer.tokenizer is nlp.spacy_token_lemmatizer
    assert transformed.shape[0] == 2
    assert transformed.shape[1] == 1
    assert vectorizer.get_feature_names_out().tolist() == [""]
    assert not np.allclose(transformed.toarray(), 0.0)


def test_mean_embedding_transformer_fit_transform_and_feature_names(runtime_nlp):
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


def test_tfidf_mean_embedding_transformer_fit_and_transform(runtime_nlp):
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
            runtime_nlp.vocab.get_vector("alpha") * alpha_weight,
            runtime_nlp.vocab.get_vector("beta") * beta_weight,
        ],
        axis=0,
    )

    np.testing.assert_allclose(transformed[0], expected_first)
    np.testing.assert_allclose(transformed[1], np.zeros(3))
    np.testing.assert_allclose(
        transformed[2], runtime_nlp.vocab.get_vector("alpha") * alpha_weight
    )
