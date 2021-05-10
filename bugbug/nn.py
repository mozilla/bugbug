# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from sklearn.base import BaseEstimator, ClassifierMixin, TransformerMixin

from bugbug.utils import numpy_to_dict

OPT_MSG_MISSING = (
    "Optional dependencies are missing, install them with: pip install bugbug[nn]\n"
)

try:
    from tensorflow.keras.preprocessing.sequence import pad_sequences
    from tensorflow.keras.preprocessing.text import Tokenizer
    from tensorflow.keras.utils import to_categorical
except ImportError:
    raise ImportError(OPT_MSG_MISSING)


class KerasTextToSequences(BaseEstimator, TransformerMixin):
    def __init__(self, maxlen, vocab_size):
        self.maxlen = maxlen
        self.vocab_size = vocab_size
        self.tokenizer = Tokenizer(num_words=vocab_size)

    def fit(self, x, y=None):
        self.tokenizer.fit_on_texts(x)
        return self

    def transform(self, data):
        sequences = self.tokenizer.texts_to_sequences(data)
        return pad_sequences(sequences, maxlen=self.maxlen)


class KerasClassifier(BaseEstimator, ClassifierMixin):
    def __init__(self, fit_params):
        self.fit_params = fit_params

    def fit(self, X, y):
        X_dict = numpy_to_dict(X)

        y = to_categorical(y)

        self.model = self.model_creator(X_dict, y)

        for (epochs, batch_size) in self.fit_params:
            self.model.fit(X_dict, y, epochs=epochs, batch_size=batch_size, verbose=1)

        return self

    def predict_proba(self, X):
        return self.model.predict(numpy_to_dict(X))

    def predict(self, X):
        return self.predict_proba(X).argmax(axis=-1)
