# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse

from bugbug import train

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--lemmatization', help='Perform lemmatization (using spaCy)', action='store_true')
    parser.add_argument('--model', nargs='?', help='Path where to store the model file')
    args = parser.parse_args()

    train.train(model=args.model, lemmatization=args.lemmatization)
