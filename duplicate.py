# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

# Using latent semantic indexing and similarity matrix

import logging

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

    def get_similar_bugs(self, query, duplicate_of, default=10):

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
        master_present = False
        for i, j in enumerate(sims):
            if duplicate_of == self.corpus[j[0]][0]:
                master_present = True
            if i >= 1 and i <= default:  # since i = 0 returns the query itself
                sim_bug_ids.append(self.corpus[j[0]][0])
            if i == 2000:
                break

        if master_present:
            return sim_bug_ids

        return False


def recall_rate():
    similarity = SimilarityLsi()
    total_bugs = 0
    hits = 0
    for bug in bugzilla.get_bugs():
        if bug["dupe_of"]:
            similar_bugs = similarity.get_similar_bugs(bug["id"], bug["dupe_of"])
            if similar_bugs:
                total_bugs += 1
                if bug["dupe_of"] in similar_bugs:
                    hits += 1

    print(f"The recall rate is {hits/total_bugs * 100} %")


if __name__ == "__main__":
    recall_rate()
