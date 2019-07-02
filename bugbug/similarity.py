# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import re
from collections import defaultdict

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors

from bugbug import bugzilla, feature_cleanup

OPT_MSG_MISSING = (
    "Optional dependencies are missing, install them with: pip install bugbug[nlp]\n"
)

try:
    import nltk
    from gensim import models, similarities
    from gensim.corpora import Dictionary
    from nltk.corpus import stopwords
    from nltk.stem.porter import PorterStemmer
except ImportError:
    raise ImportError(OPT_MSG_MISSING)

nltk.download("stopwords")

REPORTERS_TO_IGNORE = {"intermittent-bug-filer@mozilla.bugs", "wptsync@mozilla.bugs"}

cleanup_functions = [
    feature_cleanup.responses(),
    feature_cleanup.hex(),
    feature_cleanup.dll(),
    feature_cleanup.fileref(),
    feature_cleanup.url(),
    feature_cleanup.synonyms(),
    feature_cleanup.crash(),
]

# A map from bug ID to its duplicate IDs
duplicates = defaultdict(set)
all_ids = set(
    bug["id"]
    for bug in bugzilla.get_bugs()
    if bug["creator"] not in REPORTERS_TO_IGNORE and "dupeme" not in bug["keywords"]
)

for bug in bugzilla.get_bugs():
    dupes = [entry for entry in bug["duplicates"] if entry in all_ids]
    if bug["dupe_of"] in all_ids:
        dupes.append(bug["dupe_of"])

    duplicates[bug["id"]].update(dupes)
    for dupe in dupes:
        duplicates[dupe].add(bug["id"])


def get_text(bug):
    return "{} {}".format(bug["summary"], bug["comments"][0]["text"])


def text_preprocess(text, join=False):
    for func in cleanup_functions:
        text = func(text)

    text = re.sub("[^a-zA-Z0-9]", " ", text)

    ps = PorterStemmer()
    text = [
        ps.stem(word)
        for word in text.lower().split()
        if word not in set(stopwords.words("english")) and len(word) > 1
    ]
    if join:
        return " ".join(word for word in text)
    return text


class BaseSimilarity:
    def __init__(self):
        pass

    def evaluation(self):
        total_r = 0
        hits_r = 0
        total_p = 0
        hits_p = 0

        for bug in bugzilla.get_bugs():
            if duplicates[bug["id"]]:
                similar_bugs = self.get_similar_bugs(bug)

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

        print(f"Recall: {hits_r/total_r * 100}%")
        print(f"Precision: {hits_p/total_p * 100}%")


class LSISimilarity(BaseSimilarity):
    def __init__(self):
        self.corpus = []

        for bug in bugzilla.get_bugs():

            textual_features = text_preprocess(get_text(bug))
            self.corpus.append([bug["id"], textual_features])

        # Assigning unique integer ids to all words
        self.dictionary = Dictionary(text for bug_id, text in self.corpus)

        # Conversion to BoW
        corpus_final = [self.dictionary.doc2bow(text) for bug_id, text in self.corpus]

        # Initializing and applying the tfidf transformation model on same corpus,resultant corpus is of same dimensions
        tfidf = models.TfidfModel(corpus_final)
        corpus_tfidf = tfidf[corpus_final]

        # Transform TF-IDF corpus to latent 300-D space via Latent Semantic Indexing
        self.lsi = models.LsiModel(
            corpus_tfidf, id2word=self.dictionary, num_topics=300
        )
        corpus_lsi = self.lsi[corpus_tfidf]

        # Indexing the corpus
        self.index = similarities.Similarity(
            output_prefix="simdata.shdat", corpus=corpus_lsi, num_features=300
        )

    def get_similar_bugs(self, query, k=10):
        query_summary = "{} {}".format(query["summary"], query["comments"][0]["text"])
        query_summary = text_preprocess(query_summary)

        # Transforming the query to latent 300-D space
        vec_bow = self.dictionary.doc2bow(query_summary)
        vec_lsi = self.lsi[vec_bow]

        # Perform a similarity query against the corpus
        sims = self.index[vec_lsi]
        sims = sorted(enumerate(sims), key=lambda item: -item[1])

        # Get IDs of the k most similar bugs
        return [self.corpus[j[0]][0] for j in sims[:k]]


class NeighborsSimilarity(BaseSimilarity):
    def __init__(self, k=10, vectorizer=TfidfVectorizer()):
        self.vectorizer = vectorizer
        self.similarity_calculator = NearestNeighbors(n_neighbors=k)
        text = []
        self.bug_ids = []

        for bug in bugzilla.get_bugs():
            text.append(text_preprocess(get_text(bug), join=True))
            self.bug_ids.append(bug["id"])

        self.vectorizer.fit(text)
        self.similarity_calculator.fit(self.vectorizer.transform(text))

    def get_similar_bugs(self, query):

        processed_query = self.vectorizer.transform([get_text(query)])
        _, indices = self.similarity_calculator.kneighbors(processed_query)

        return [
            self.bug_ids[ind] for ind in indices[0] if self.bug_ids[ind] != query["id"]
        ]
