# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import marisa_trie
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import TfidfVectorizer

D = [{"foo": 1, "bar": 2}, {"pho": 3, "baz": 4}, {"foo": 5, "bra": 6, "arm": 7}]
E = [
    "this is the first sentence",
    "the second sentence is this one",
    "is this the final sentence",
    "it turns out it was not",
]
# pipe = Pipeline(
#     [
#         ("Test",
#             ColumnTransformer(
#                 [
#                     ("Dict", DictVectorizer(), (-1, 1)),
#                     ("Tfidf", TfidfVectorizer(), (-1, 1))
#                 ]
#             )
#          )
#     ]
# )


v = DictVectorizer()

X = v.fit_transform(D).toarray()
# print(X, "\n")
print(v.get_feature_names_out(), "\n")
print(v.vocabulary_.keys())

w = TfidfVectorizer()
# Y = pipe.fit_transform(D, E)
# print(Y)
transformed = w.fit_transform(E)
# print(transformed)
print(w.get_feature_names_out())
print(w.vocabulary_.keys())

t = marisa_trie.Trie(v.vocabulary_)
s = marisa_trie.Trie(w.vocabulary_)
# t = Trie(key for key in v.get_feature_names_out())
# for item in v.get_feature_names_out():
#     t.update(dict(s.split('=') for s in [item]))

print(t.keys())
print(t)

# s = Trie()
# for item in w.get_feature_names_out():
#     for letter in item:
#         s[letter] = letter

print(s.keys())
print(s)
