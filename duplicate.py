# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

# Using latent semantic indexing and similarity matrix

import logging
from collections import defaultdict

import nltk
from gensim import models, similarities
from gensim.corpora import Dictionary
from nltk.corpus import stopwords
from nltk.stem.porter import PorterStemmer

from bugbug import bugzilla

logging.basicConfig(
    format="%(asctime)s : %(levelname)s : %(message)s", level=logging.INFO
)

nltk.download("stopwords")

# A map from bug id to its duplicate ids
all_ids = defaultdict(int)
duplicates = defaultdict(set)

for bug in bugzilla.get_bugs():
    all_ids[bug["id"]] = 1

for bug in bugzilla.get_bugs():
    dupes = set([bugs for bugs in bug["duplicates"] if all_ids[bugs] != 0])
    if all_ids[bug["dupe_of"]] != 0:
        dupes.add(bug["dupe_of"])

    duplicates[bug["id"]] |= dupes
    for dupe in dupes:
        duplicates[dupe].add(bug["id"])


class SimilarityLsi:
    def __init__(self):

        self.corpus = []

        for bug in bugzilla.get_bugs():
            self.corpus.append([bug["id"], bug["summary"]])

        # cleaning the text
        self.ps = PorterStemmer()
        texts = [
            [
                self.ps.stem(word)
                for word in summary.lower().split()
                if word not in set(stopwords.words("english"))
            ]
            for bug_id, summary in self.corpus
        ]

        # Assigning unique integer ids to all words
        self.dictionary = Dictionary(texts)

        # conversion to bow
        corpus_final = [self.dictionary.doc2bow(text) for text in texts]

        # initializing and applying the tfidf transformation model on same corpus,resultant corpus is of same dimensions
        tfidf = models.TfidfModel(corpus_final)
        corpus_tfidf = tfidf[corpus_final]

        # transform tfidf corpus to latent 300-D space via LATENT SEMANTIC INDEXING
        self.lsi = models.LsiModel(
            corpus_tfidf, id2word=self.dictionary, num_topics=300
        )
        corpus_lsi = self.lsi[corpus_tfidf]

        # indexing the corpus
        self.index = similarities.Similarity(
            output_prefix="simdata.shdat", corpus=corpus_lsi, num_features=300
        )

    def get_similar_bugs(self, query, default=10):

        # transforming the query to latent 300-D space
        for bug_id, summary in self.corpus:
            if bug_id == query:
                query_summary = [
                    self.ps.stem(word)
                    for word in summary.lower().split()
                    if word not in set(stopwords.words("english"))
                ]
                break
        vec_bow = self.dictionary.doc2bow(query_summary)
        vec_lsi = self.lsi[vec_bow]

        # perform a similarity query against the corpus
        sims = self.index[vec_lsi]
        sims = sorted(enumerate(sims), key=lambda item: -item[1])

        # bug_id of the k most similar summaries
        sim_bug_ids = []
        for i, j in enumerate(sims):
            if i >= 1 and i <= default:  # since i = 0 returns the query itself
                sim_bug_ids.append(self.corpus[j[0]][0])
                continue
            if i > default:
                break

        return sim_bug_ids


def evaluation():
    similarity = SimilarityLsi()
    total_r = 0
    hits_r = 0
    total_p = 0
    hits_p = 0

    for bug in bugzilla.get_bugs():
        if duplicates[bug["id"]]:
            similar_bugs = similarity.get_similar_bugs(bug["id"])

            # Recall
            for item in duplicates[bug["id"]]:
                total_r += 1
                if item in similar_bugs:
                    hits_r += 1

            # Precision
            for element in similar_bugs:
                total_p += 1
                if element in duplicates[bug["id"]]:
                    hits_p += 1

    print(f"Recall: {hits_r/total_r * 100} %")
    print(f"Precision: {hits_p/total_p * 100} %")


if __name__ == "__main__":
    evaluation()
