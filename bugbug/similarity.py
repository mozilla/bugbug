# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import abc
import bisect
import logging
import os
import pickle
import random
import re
from collections import defaultdict
from functools import lru_cache
from itertools import chain

import numpy as np
from pyemd import emd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors
from tqdm import tqdm

from bugbug import bugzilla, feature_cleanup
from bugbug.models.duplicate import DuplicateModel
from bugbug.utils import download_check_etag, zstd_decompress

OPT_MSG_MISSING = (
    "Optional dependencies are missing, install them with: pip install bugbug[nlp]\n"
)

try:
    import gensim
    import nltk
    import spacy
    from elasticsearch import Elasticsearch
    from elasticsearch.helpers import bulk
    from gensim import models, similarities
    from gensim.corpora import Dictionary
    from gensim.matutils import sparse2full
    from gensim.models import TfidfModel, Word2Vec, WordEmbeddingSimilarityIndex
    from gensim.models.ldamodel import LdaModel
    from gensim.similarities import SoftCosineSimilarity, SparseTermSimilarityMatrix
    from gensim.summarization.bm25 import BM25
    from nltk.corpus import stopwords
    from nltk.stem.porter import PorterStemmer
    from nltk.tokenize import word_tokenize
    from wmd import WMD
except ImportError:
    raise ImportError(OPT_MSG_MISSING)

nltk.download("stopwords")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def get_nlp():
    return spacy.load("en_core_web_sm")


REPORTERS_TO_IGNORE = {"intermittent-bug-filer@mozilla.bugs", "wptsync@mozilla.bugs"}


def download_similarity_model(model_name):
    path = f"{model_name_to_class[model_name].__name__.lower()}.similaritymodel"
    url = f"https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.train_similarity.latest/artifacts/public/{path}.zst"

    logger.info(f"Downloading similarity model from {url}...")
    updated = download_check_etag(url)
    if updated:
        zstd_decompress(path)
        os.remove(f"{path}.zst")
    assert os.path.exists(path), "Decompressed file exists"
    return path


def download_and_load_similarity_model(model_name):
    path = download_similarity_model(model_name)
    return model_name_to_class[model_name].load(path)


class BaseSimilarity(abc.ABC):
    def __init__(
        self,
        cleanup_urls=True,
        nltk_tokenizer=False,
        confidence_threshold=0.8,
        end_to_end=False,
    ):
        self.cleanup_functions = [
            feature_cleanup.responses(),
            feature_cleanup.hex(),
            feature_cleanup.dll(),
            feature_cleanup.fileref(),
            feature_cleanup.synonyms(),
            feature_cleanup.crash(),
        ]
        if cleanup_urls:
            self.cleanup_functions.append(feature_cleanup.url())

        self.nltk_tokenizer = nltk_tokenizer
        self.confidence_threshold = confidence_threshold

        self.duplicatemodel = (
            DuplicateModel.load("duplicatemodel") if end_to_end else None
        )

    def get_text(self, bug, all_comments=False):
        if all_comments:
            comments = " ".join(comment["text"] for comment in bug["comments"])
        else:
            comments = bug["comments"][0]["text"]

        return "{} {}".format(bug["summary"], comments)

    def get_similar_bugs(self, bug):
        similar_bug_ids = self.search_similar_bugs(bug)
        if self.duplicatemodel:
            similar_bugs = [
                bug for bug in bugzilla.get_bugs() if bug["id"] in similar_bug_ids
            ]
            bug_couples = [(bug, similar_bug) for similar_bug in similar_bugs]
            probs_bug_couples = sorted(
                zip(
                    self.duplicatemodel.classify(bug_couples, probabilities=True),
                    bug_couples,
                ),
                key=lambda v: -v[0][1],
            )

            similar_bug_ids = [
                similar_bug["id"]
                for prob, (bug, similar_bug) in probs_bug_couples
                if prob[1] > self.confidence_threshold
            ]

        return similar_bug_ids

    def text_preprocess(self, text, stemming=True, lemmatization=False, join=False):
        for func in self.cleanup_functions:
            text = func(text)

        text = re.sub("[^a-zA-Z0-9]", " ", text)

        if lemmatization:
            text = [word.lemma_ for word in get_nlp()(text)]
        elif stemming:
            ps = PorterStemmer()
            tokenized_text = (
                word_tokenize(text.lower())
                if self.nltk_tokenizer
                else text.lower().split()
            )
            text = [
                ps.stem(word)
                for word in tokenized_text
                if word not in set(stopwords.words("english")) and len(word) > 1
            ]
        else:
            text = text.split()

        if join:
            return " ".join(word for word in text)
        return text

    def evaluation(self):
        # A map from bug ID to its duplicate IDs
        duplicates = defaultdict(set)
        all_ids = set(
            bug["id"]
            for bug in bugzilla.get_bugs()
            if bug["creator"] not in REPORTERS_TO_IGNORE
            and "dupeme" not in bug["keywords"]
        )

        for bug in bugzilla.get_bugs():
            dupes = [entry for entry in bug["duplicates"] if entry in all_ids]
            if bug["dupe_of"] in all_ids:
                dupes.append(bug["dupe_of"])

            duplicates[bug["id"]].update(dupes)
            for dupe in dupes:
                duplicates[dupe].add(bug["id"])

        total_r = 0
        hits_r = 0
        total_p = 0
        hits_p = 0

        recall_rate_1 = 0
        recall_rate_5 = 0
        recall_rate_10 = 0
        precision_rate_1 = 0
        precision_rate_5 = 0
        precision_rate_10 = 0

        queries = 0
        apk = []
        for bug in tqdm(bugzilla.get_bugs()):
            if duplicates[bug["id"]]:
                score = 0
                num_hits = 0
                queries += 1
                similar_bugs = self.get_similar_bugs(bug)[:10]

                # Recall
                for idx, item in enumerate(duplicates[bug["id"]]):
                    total_r += 1
                    if item in similar_bugs:
                        hits_r += 1
                        if idx == 0:
                            recall_rate_1 += 1
                        if idx < 5:
                            recall_rate_5 += 1
                        if idx < 10:
                            recall_rate_10 += 1

                # Precision
                for idx, element in enumerate(similar_bugs):
                    total_p += 1
                    if element in duplicates[bug["id"]]:
                        hits_p += 1
                        if idx == 0:
                            precision_rate_1 += 1

                        if idx < 5:
                            precision_rate_5 += 1 / 5

                        if idx < 10:
                            precision_rate_10 += 1 / 10

                        num_hits += 1
                        score += num_hits / (idx + 1)

                apk.append(score / min(len(duplicates[bug["id"]]), 10))

        print(f"Recall @ 1: {recall_rate_1/total_r * 100}%")
        print(f"Recall @ 5: {recall_rate_5/total_r * 100}%")
        print(f"Recall @ 10: {recall_rate_10/total_r * 100}%")
        print(f"Precision @ 1: {precision_rate_1/queries * 100}%")
        print(f"Precision @ 5: {precision_rate_5/queries * 100}%")
        print(f"Precision @ 10: {precision_rate_10/queries * 100}%")
        print(f"Recall: {hits_r/total_r * 100}%")
        print(f"Precision: {hits_p/total_p * 100}%")
        print(f"MAP@k : {np.mean(apk) * 100}%")

    @abc.abstractmethod
    def get_distance(self, query1, query2):
        return

    def save(self):
        path = f"{self.__class__.__name__.lower()}.similaritymodel"
        with open(path, "wb") as f:
            pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)
        return path

    @staticmethod
    def load(model_file_name):
        with open(model_file_name, "rb") as f:
            return pickle.load(f)


class LSISimilarity(BaseSimilarity):
    def __init__(
        self, cleanup_urls=True, nltk_tokenizer=False, confidence_threshold=0.8
    ):
        super().__init__(
            cleanup_urls=cleanup_urls,
            nltk_tokenizer=nltk_tokenizer,
            confidence_threshold=confidence_threshold,
        )
        self.corpus = []

        for bug in bugzilla.get_bugs():
            textual_features = self.text_preprocess(self.get_text(bug))
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

    def search_similar_bugs(self, query, k=10):
        query_summary = "{} {}".format(query["summary"], query["comments"][0]["text"])
        query_summary = self.text_preprocess(query_summary)

        # Transforming the query to latent 300-D space
        vec_bow = self.dictionary.doc2bow(query_summary)
        vec_lsi = self.lsi[vec_bow]

        # Perform a similarity query against the corpus
        sims = self.index[vec_lsi]
        sims = sorted(enumerate(sims), key=lambda item: -item[1])

        # Get IDs of the k most similar bugs
        return [
            self.corpus[j[0]][0]
            for j in sims[:k]
            if self.corpus[j[0]][0] != query["id"]
        ]

    def get_distance(self, query1, query2):
        raise NotImplementedError


class NeighborsSimilarity(BaseSimilarity):
    def __init__(
        self,
        k=10,
        vectorizer=TfidfVectorizer(),
        cleanup_urls=True,
        nltk_tokenizer=False,
        confidence_threshold=0.8,
    ):
        super().__init__(
            cleanup_urls=cleanup_urls,
            nltk_tokenizer=nltk_tokenizer,
            confidence_threshold=confidence_threshold,
        )
        self.vectorizer = vectorizer
        self.similarity_calculator = NearestNeighbors(n_neighbors=k)
        text = []
        self.bug_ids = []

        for bug in bugzilla.get_bugs():
            text.append(self.text_preprocess(self.get_text(bug), join=True))
            self.bug_ids.append(bug["id"])

        self.vectorizer.fit(text)
        self.similarity_calculator.fit(self.vectorizer.transform(text))

    def search_similar_bugs(self, query):
        processed_query = self.vectorizer.transform([self.get_text(query)])
        _, indices = self.similarity_calculator.kneighbors(processed_query)

        return [
            self.bug_ids[ind] for ind in indices[0] if self.bug_ids[ind] != query["id"]
        ]

    def get_distance(self, query1, query2):
        raise NotImplementedError


class Word2VecSimilarityBase(BaseSimilarity):
    def __init__(
        self,
        cut_off=0.2,
        cleanup_urls=True,
        nltk_tokenizer=False,
        confidence_threshold=0.8,
    ):
        super().__init__(
            cleanup_urls=cleanup_urls,
            nltk_tokenizer=nltk_tokenizer,
            confidence_threshold=confidence_threshold,
        )
        self.corpus = []
        self.bug_ids = []
        self.cut_off = cut_off
        for bug in bugzilla.get_bugs():
            self.corpus.append(self.text_preprocess(self.get_text(bug)))
            self.bug_ids.append(bug["id"])

        indexes = list(range(len(self.corpus)))
        random.shuffle(indexes)
        self.corpus = [self.corpus[idx] for idx in indexes]
        self.bug_ids = [self.bug_ids[idx] for idx in indexes]

        self.w2vmodel = Word2Vec(self.corpus, size=100, min_count=5)
        self.w2vmodel.init_sims(replace=True)


class Word2VecWmdSimilarity(Word2VecSimilarityBase):
    def __init__(
        self,
        cut_off=0.2,
        cleanup_urls=True,
        nltk_tokenizer=False,
        confidence_threshold=0.8,
    ):
        super().__init__(
            cleanup_urls=cleanup_urls,
            nltk_tokenizer=nltk_tokenizer,
            confidence_threshold=confidence_threshold,
        )

    # word2vec.wmdistance calculates only the euclidean distance. To get the cosine distance,
    # we're using the function with a few subtle changes. We compute the cosine distances
    # in the search_similar_bugs method and use this inside the wmdistance method.
    def wmdistance(self, document1, document2, all_distances, distance_metric="cosine"):
        model = self.w2vmodel
        if len(document1) == 0 or len(document2) == 0:
            print(
                "At least one of the documents had no words that were in the vocabulary. Aborting (returning inf)."
            )
            return float("inf")

        dictionary = gensim.corpora.Dictionary(documents=[document1, document2])
        vocab_len = len(dictionary)

        # Sets for faster look-up.
        docset1 = set(document1)
        docset2 = set(document2)

        distance_matrix = np.zeros((vocab_len, vocab_len), dtype=np.double)

        for i, t1 in dictionary.items():
            for j, t2 in dictionary.items():
                if t1 not in docset1 or t2 not in docset2:
                    continue

                if distance_metric == "euclidean":
                    distance_matrix[i, j] = np.sqrt(
                        np.sum((model.wv[t1] - model.wv[t2]) ** 2)
                    )
                elif distance_metric == "cosine":
                    distance_matrix[i, j] = all_distances[model.wv.vocab[t2].index, i]

        if np.sum(distance_matrix) == 0.0:
            print("The distance matrix is all zeros. Aborting (returning inf).")
            return float("inf")

        def nbow(document):
            d = np.zeros(vocab_len, dtype=np.double)
            nbow = dictionary.doc2bow(document)
            doc_len = len(document)
            for idx, freq in nbow:
                d[idx] = freq / float(doc_len)
            return d

        d1 = nbow(document1)
        d2 = nbow(document2)

        return emd(d1, d2, distance_matrix)

    def calculate_all_distances(self, words):
        return np.array(
            1.0
            - np.dot(
                self.w2vmodel.wv.vectors_norm,
                self.w2vmodel.wv.vectors_norm[
                    [self.w2vmodel.wv.vocab[word].index for word in words]
                ].transpose(),
            ),
            dtype=np.double,
        )

    def search_similar_bugs(self, query):
        words = self.text_preprocess(self.get_text(query))
        words = [word for word in words if word in self.w2vmodel.wv.vocab]

        all_distances = self.calculate_all_distances(words)

        distances = []
        for i in range(len(self.corpus)):
            cleaned_corpus = [
                word for word in self.corpus[i] if word in self.w2vmodel.wv.vocab
            ]
            indexes = [self.w2vmodel.wv.vocab[word].index for word in cleaned_corpus]
            if len(indexes) != 0:
                word_dists = all_distances[indexes]
                rwmd = max(
                    np.sum(np.min(word_dists, axis=0)),
                    np.sum(np.min(word_dists, axis=1)),
                )

                distances.append((self.bug_ids[i], rwmd))

        distances.sort(key=lambda v: v[1])

        confirmed_distances_ids = []
        confirmed_distances = []

        for i, (doc_id, rwmd_distance) in enumerate(distances):
            if (
                len(confirmed_distances) >= 10
                and rwmd_distance > confirmed_distances[10 - 1]
            ):
                break

            doc_words_clean = [
                word
                for word in self.corpus[self.bug_ids.index(doc_id)]
                if word in self.w2vmodel.wv.vocab
            ]
            wmd = self.wmdistance(words, doc_words_clean, all_distances)

            j = bisect.bisect(confirmed_distances, wmd)
            confirmed_distances.insert(j, wmd)
            confirmed_distances_ids.insert(j, doc_id)

        similarities = zip(confirmed_distances_ids, confirmed_distances)

        return [
            similar[0]
            for similar in sorted(similarities, key=lambda v: v[1])[:10]
            if similar[0] != query["id"] and similar[1] < self.cut_off
        ]

    def get_distance(self, query1, query2):
        words1 = self.text_preprocess(self.get_text(query1))
        words1 = [word for word in words1 if word in self.w2vmodel.wv.vocab]
        words2 = self.text_preprocess(self.get_text(query2))
        words2 = [word for word in words2 if word in self.w2vmodel.wv.vocab]

        all_distances = self.calculate_all_distances(words1)

        wmd = self.wmdistance(words1, words2, all_distances)

        return wmd


class Word2VecWmdRelaxSimilarity(Word2VecSimilarityBase):
    def __init__(
        self,
        cut_off=0.2,
        cleanup_urls=True,
        nltk_tokenizer=False,
        confidence_threshold=0.8,
    ):
        super().__init__(
            cleanup_urls=cleanup_urls,
            nltk_tokenizer=nltk_tokenizer,
            confidence_threshold=confidence_threshold,
        )
        self.dictionary = Dictionary(self.corpus)
        self.tfidf = TfidfModel(dictionary=self.dictionary)

    def search_similar_bugs(self, query):
        query = self.text_preprocess(self.get_text(query))
        words = [
            word for word in set(chain(query, *self.corpus)) if word in self.w2vmodel.wv
        ]
        indices, words = zip(
            *sorted(
                (
                    (index, word)
                    for (index, _), word in zip(self.dictionary.doc2bow(words), words)
                )
            )
        )
        query = dict(self.tfidf[self.dictionary.doc2bow(query)])
        query = [
            (new_index, query[dict_index])
            for new_index, dict_index in enumerate(indices)
            if dict_index in query
        ]
        documents = [
            dict(self.tfidf[self.dictionary.doc2bow(document)])
            for document in self.corpus
        ]
        documents = [
            [
                (new_index, document[dict_index])
                for new_index, dict_index in enumerate(indices)
                if dict_index in document
            ]
            for document in documents
        ]
        embeddings = np.array(
            [self.w2vmodel.wv[word] for word in words], dtype=np.float32
        )
        nbow = dict(
            (
                (index, list(chain([None], zip(*document))))
                for index, document in enumerate(documents)
                if document != []
            )
        )
        nbow["query"] = tuple([None] + list(zip(*query)))
        distances = WMD(embeddings, nbow, vocabulary_min=1).nearest_neighbors("query")

        return [
            self.bug_ids[distance[0]]
            for distance in distances
            if self.bug_ids[distance[0]] != query["id"]
        ]

    def get_distance(self, query1, query2):
        query1 = self.text_preprocess(self.get_text(query1))
        query2 = self.text_preprocess(self.get_text(query2))

        words = [
            word
            for word in set(chain(query1, query2, *self.corpus))
            if word in self.w2vmodel.wv
        ]
        indices, words = zip(
            *sorted(
                (
                    (index, word)
                    for (index, _), word in zip(self.dictionary.doc2bow(words), words)
                )
            )
        )
        query1 = dict(self.tfidf[self.dictionary.doc2bow(query1)])
        query2 = dict(self.tfidf[self.dictionary.doc2bow(query2)])

        query1 = [
            (new_index, query1[dict_index])
            for new_index, dict_index in enumerate(indices)
            if dict_index in query1
        ]
        query2 = [
            (new_index, query2[dict_index])
            for new_index, dict_index in enumerate(indices)
            if dict_index in query2
        ]
        embeddings = np.array(
            [self.w2vmodel.wv[word] for word in words], dtype=np.float32
        )
        nbow = {}
        nbow["query1"] = tuple([None] + list(zip(*query1)))
        nbow["query2"] = tuple([None] + list(zip(*query2)))
        distances = WMD(embeddings, nbow, vocabulary_min=1).nearest_neighbors("query1")

        return distances[0][1]


class Word2VecSoftCosSimilarity(Word2VecSimilarityBase):
    def __init__(
        self,
        cut_off=0.2,
        cleanup_urls=True,
        nltk_tokenizer=False,
        confidence_threshold=0.8,
    ):
        super().__init__(
            cleanup_urls=cleanup_urls,
            nltk_tokenizer=nltk_tokenizer,
            confidence_threshold=confidence_threshold,
        )

        terms_idx = WordEmbeddingSimilarityIndex(self.w2vmodel.wv)
        self.dictionary = Dictionary(self.corpus)

        bow = [self.dictionary.doc2bow(doc) for doc in self.corpus]

        similarity_matrix = SparseTermSimilarityMatrix(terms_idx, self.dictionary)
        self.softcosinesimilarity = SoftCosineSimilarity(
            bow, similarity_matrix, num_best=10
        )

    def search_similar_bugs(self, query):
        similarities = self.softcosinesimilarity[
            self.dictionary.doc2bow(self.text_preprocess(self.get_text(query)))
        ]
        return [
            self.bug_ids[similarity[0]]
            for similarity in similarities
            if self.bug_ids[similarity[0]] != query["id"]
        ]

    def get_distance(self, query1, query2):
        raise NotImplementedError


class BM25Similarity(BaseSimilarity):
    def __init__(
        self, cleanup_urls=True, nltk_tokenizer=False, confidence_threshold=0.8
    ):
        super().__init__(
            cleanup_urls=cleanup_urls,
            nltk_tokenizer=nltk_tokenizer,
            confidence_threshold=confidence_threshold,
        )
        self.corpus = []
        self.bug_ids = []

        for bug in bugzilla.get_bugs():
            self.corpus.append(self.text_preprocess(self.get_text(bug)))
            self.bug_ids.append(bug["id"])

        indexes = list(range(len(self.corpus)))
        random.shuffle(indexes)
        self.corpus = [self.corpus[idx] for idx in indexes]
        self.bug_ids = [self.bug_ids[idx] for idx in indexes]

        self.model = BM25(self.corpus)

    def search_similar_bugs(self, query):
        distances = self.model.get_scores(self.text_preprocess(self.get_text(query)))
        id_dist = zip(self.bug_ids, distances)

        id_dist = sorted(list(id_dist), reverse=True, key=lambda v: v[1])

        return [distance[0] for distance in id_dist[:10]]

    def get_distance(self, query1, query2):
        raise NotImplementedError


class LDASimilarity(BaseSimilarity):
    def __init__(
        self, cleanup_urls=True, nltk_tokenizer=False, confidence_threshold=0.8
    ):
        super().__init__(
            cleanup_urls=cleanup_urls,
            nltk_tokenizer=nltk_tokenizer,
            confidence_threshold=confidence_threshold,
        )
        self.corpus = []
        self.bug_ids = []
        for bug in bugzilla.get_bugs():
            self.corpus.append(self.text_preprocess(self.get_text(bug)))
            self.bug_ids.append(bug["id"])

        indexes = list(range(len(self.corpus)))
        random.shuffle(indexes)
        self.corpus = [self.corpus[idx] for idx in indexes]
        self.bug_ids = [self.bug_ids[idx] for idx in indexes]

        self.dictionary = Dictionary(self.corpus)

        self.model = LdaModel([self.dictionary.doc2bow(text) for text in self.corpus])

    def search_similar_bugs(self, query):
        query = self.text_preprocess(self.get_text(query))

        dense1 = sparse2full(
            self.model[self.dictionary.doc2bow(query)], self.model.num_topics
        )
        distances = []

        for idx in range(len(self.corpus)):
            dense2 = sparse2full(
                self.model[self.dictionary.doc2bow(self.corpus[idx])],
                self.model.num_topics,
            )
            hellinger_distance = np.sqrt(
                0.5 * ((np.sqrt(dense1) - np.sqrt(dense2)) ** 2).sum()
            )

            distances.append((self.bug_ids[idx], hellinger_distance))

        distances.sort(key=lambda v: v[1])

        return [distance[0] for distance in distances[:10]]

    def get_distance(self, query1, query2):
        raise NotImplementedError


class ElasticSearchSimilarity(BaseSimilarity):
    def __init__(
        self, cleanup_urls=True, nltk_tokenizer=False, confidence_threshold=0.8
    ):
        super().__init__(
            cleanup_urls=cleanup_urls,
            nltk_tokenizer=nltk_tokenizer,
            confidence_threshold=confidence_threshold,
        )
        self.elastic_search = Elasticsearch()
        assert (
            self.elastic_search.ping()
        ), "Check if Elastic Search Server is running by visiting http://localhost:9200"

    def make_documents(self):
        for bug in bugzilla.get_bugs():
            yield {
                "_index": "bugbug",
                "_type": "_doc",
                "bug_id": bug["id"],
                "description": self.text_preprocess(
                    self.get_text(bug, all_comments=True), stemming=False, join=True
                ),
            }

    def index(self):
        self.elastic_search.indices.delete(index="bugbug", ignore=[400, 404])
        bulk(self.elastic_search, self.make_documents())

    def search_similar_bugs(self, query):
        find_similar = self.text_preprocess(
            self.get_text(query, all_comments=True), stemming=False, join=True
        )

        es_query = {
            "more_like_this": {
                "fields": ["description"],
                "like": find_similar,
                "min_term_freq": 1,
                "max_query_terms": 25,
                "min_doc_freq": 2,
            }
        }

        result = self.elastic_search.search(index="bugbug", body={"query": es_query})

        top_similar = [
            result["hits"]["hits"][i]["_source"]["bug_id"]
            for i in range(len(result["hits"]["hits"]))
            if result["hits"]["hits"][i]["_source"]["bug_id"] != query["id"]
        ]
        return top_similar

    def get_distance(self, query1, query2):
        raise NotImplementedError


model_name_to_class = {
    "lsi": LSISimilarity,
    "neighbors_tfidf": NeighborsSimilarity,
    "neighbors_tfidf_bigrams": NeighborsSimilarity,
    "word2vec_wmdrelax": Word2VecWmdRelaxSimilarity,
    "word2vec_wmd": Word2VecWmdSimilarity,
    "word2vec_softcos": Word2VecSoftCosSimilarity,
    "bm25": BM25Similarity,
    "lda": LDASimilarity,
    "elasticsearch": ElasticSearchSimilarity,
}
