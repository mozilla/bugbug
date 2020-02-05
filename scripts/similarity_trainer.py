#!/usr/bin/env python
# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import os
import sys
from logging import INFO, basicConfig, getLogger

from sklearn.feature_extraction.text import TfidfVectorizer

from bugbug import bugzilla, db, similarity
from bugbug.utils import zstd_compress

basicConfig(level=INFO)
logger = getLogger(__name__)


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
    return parser.parse_args(args)


def main():
    args = parse_args(sys.argv[1:])

    logger.info("Downloading bugs database...")

    assert db.download(bugzilla.BUGS_DB)

    if args.algorithm == "elasticsearch":
        model = similarity.model_name_to_class[args.algorithm](
            cleanup_urls=args.cleanup_urls, nltk_tokenizer=args.nltk_tokenizer
        )
        model.index()
    else:
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

        path = model.save()
        assert os.path.exists(path)
        zstd_compress(path)


if __name__ == "__main__":
    main()
