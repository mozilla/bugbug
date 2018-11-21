# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import spacy
from sklearn.feature_extraction.text import TfidfVectorizer

nlp = spacy.load('en')


def spacy_token_lemmatizer(text):
    if len(text) > nlp.max_length:
        text = text[:nlp.max_length - 1]
    doc = nlp(text)
    return [token.lemma_ for token in doc]


class SpacyVectorizer(TfidfVectorizer):
    def __init__(self, *args, **kwargs):
        super().__init__(tokenizer=spacy_token_lemmatizer, *args, **kwargs)
