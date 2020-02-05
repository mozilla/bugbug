# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import sys

from sklearn.feature_extraction.text import TfidfVectorizer

from bugbug import similarity


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--algorithm",
        help="Similarity algorithm to use",
        choices=similarity.model_name_to_class.keys(),
    )
    parser.add_argument(
        "--disable-url-cleanup",
        help="Don't cleanup urls when training the similarity model",
        dest="cleanup_urls",
        default=True,
        action="store_false",
    )
    parser.add_argument(
        "--nltk_tokenizer",
        help="Use nltk's tokenizer for text preprocessing",
        dest="nltk_tokenizer",
        default=False,
    )
    parser.add_argument(
        "--index",
        help="Create/Recreate a database in Elastic Search",
        action="store_true",
    )
    return parser.parse_args(args)


def main(args):
    if args.algorithm == "neighbors_tfidf_bigrams":
        model = similarity.model_name_to_class[args.algorithm](
            vectorizer=TfidfVectorizer(ngram_range=(1, 2)),
            cleanup_urls=args.cleanup_urls,
            nltk_tokenizer=args.nltk_tokenizer,
        )
    else:
        model = similarity.model_name_to_class[args.algorithm](
            cleanup_urls=args.cleanup_urls, nltk_tokenizer=args.nltk_tokenizer
        )
    if args.algorithm == "elasticsearch" and args.index:
        model.index()

    model.evaluation()


if __name__ == "__main__":
    main(parse_args(sys.argv[1:]))
