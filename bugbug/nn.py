# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from keras.preprocessing.sequence import pad_sequences
from keras.preprocessing.text import Tokenizer
from sklearn.base import BaseEstimator
from sklearn.base import ClassifierMixin
from sklearn.base import TransformerMixin

from bugbug.utils import numpy_to_dict


class KerasTextToSequences(BaseEstimator, TransformerMixin):
    def __init__(self, maxlen, vocab_size):
        self.maxlen = maxlen
        self.tokenizer = Tokenizer(num_words=vocab_size)

    def fit(self, x, y=None):
        self.tokenizer.fit_on_texts(x)
        return self

    def transform(self, data):
        sequences = self.tokenizer.texts_to_sequences(data)
        return pad_sequences(sequences, maxlen=self.maxlen)


class KerasModel(BaseEstimator, ClassifierMixin):
    def __init__(self, epochs, batch_size):
        self.epochs = epochs
        self.batch_size = batch_size

    def fit(self, X, y):
        X_dict = numpy_to_dict(X)

        self.model = self.model_creator(X_dict, y)

        self.model.fit(X_dict, y, epochs=self.epochs, batch_size=self.batch_size, verbose=1)

        return self

    def predict_proba(self, X):
        return self.model.predict(numpy_to_dict(X))

    def predict(self, X):
        return self.predict_proba(X).argmax(axis=-1)
