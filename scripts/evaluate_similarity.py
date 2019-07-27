# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import sys

from sklearn.feature_extraction.text import TfidfVectorizer

from bugbug.similarity import LSISimilarity, NeighborsSimilarity, Word2VecWmdSimilarity


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--algorithm",
        help="Similarity algorithm to use",
        choices=["lsi", "neighbors_tfidf", "neighbors_tfidf_bigrams", "word2vec_wmd"],
    )
    parser.add_argument(
        "--disable-url-cleanup",
        help="Don't cleanup urls when training the similarity model",
        dest="cleanup_urls",
        default=True,
        action="store_false",
    )
    return parser.parse_args(args)


def main(args):
    if args.algorithm == "lsi":
        model_creator = LSISimilarity
    elif args.algorithm == "neighbors_tfidf":
        model_creator = NeighborsSimilarity
    elif args.algorithm == "neighbors_tfidf_bigrams":

        def model_creator(**kwargs):
            kwargs["vectorizer"] = TfidfVectorizer(ngram_range=(1, 2))
            return NeighborsSimilarity(**kwargs)

    elif args.algorithm == "word2vec_wmd":
        model_creator = Word2VecWmdSimilarity

    model = model_creator(cleanup_urls=args.cleanup_urls)
    model.evaluation()


if __name__ == "__main__":
    main(parse_args(sys.argv[1:]))
