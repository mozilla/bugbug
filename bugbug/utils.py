# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import collections

from sklearn.base import BaseEstimator
from sklearn.base import TransformerMixin


class DictSelector(BaseEstimator, TransformerMixin):
    def __init__(self, key):
        self.key = key

    def fit(self, x, y=None):
        return self

    def transform(self, data):
        return (elem[self.key] for elem in data)


def consume(iterator):
    collections.deque(iterator, maxlen=0)
