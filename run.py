# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse

from bugbug import bugzilla
from bugbug import classify
from bugbug import labels
from bugbug import train

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--lemmatization', help='Perform lemmatization (using spaCy)', action='store_true')
    parser.add_argument('--download', help='Download data required for training', action='store_true')
    parser.add_argument('--train', help='Perform training', action='store_true')
    parser.add_argument('--goal', help='Goal of the classifier', choices=['bug', 'regression', 'tracking'], default='bug')
    args = parser.parse_args()

    if args.download:
        bug_ids = labels.get_all_bug_ids()
        bugzilla.download_bugs(bug_ids)

    model = '{}.model'.format(args.goal)

    if args.train:
        if args.goal == 'bug':
            classes = labels.get_bugbug_labels(kind='bug', augmentation=True)
        elif args.goal == 'regression':
            classes = labels.get_bugbug_labels(kind='regression', augmentation=True)
        elif args.goal == 'tracking':
            classes = labels.get_tracking_labels()

        train.train(classes, model=model, lemmatization=args.lemmatization)

    classify.init(model)

    for bug in bugzilla.get_bugs():
        print('https://bugzilla.mozilla.org/show_bug.cgi?id={} - {}'.format(bug['id'], bug['summary']))
        c = classify.classify(bug)
        if c == 1:
            print('Positive!')
        else:
            print('Negative!')
        input()
