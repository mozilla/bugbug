# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse

from bugbug import bugzilla
from bugbug import db
from bugbug import repository  # noqa

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--lemmatization', help='Perform lemmatization (using spaCy)', action='store_true')
    parser.add_argument('--train', help='Perform training', action='store_true')
    parser.add_argument('--goal', help='Goal of the classifier', choices=['bug', 'regression', 'tracking', 'qaneeded', 'uplift', 'component'], default='bug')
    parser.add_argument('--classify', help='Perform evaluation', action='store_true')
    args = parser.parse_args()

    model_file_name = '{}model'.format(args.goal)

    if args.goal == 'bug':
        from bugbug.models.bug import BugModel
        model_class = BugModel
    elif args.goal == 'regression':
        from bugbug.models.regression import RegressionModel
        model_class = RegressionModel
    elif args.goal == 'tracking':
        from bugbug.models.tracking import TrackingModel
        model_class = TrackingModel
    elif args.goal == 'qaneeded':
        from bugbug.models.qaneeded import QANeededModel
        model_class = QANeededModel
    elif args.goal == 'uplift':
        from bugbug.models.uplift import UpliftModel
        model_class = UpliftModel
    elif args.goal == 'component':
        from bugbug.models.component import ComponentModel
        model_class = ComponentModel

    if args.train:
        db.download()

        model = model_class(args.lemmatization)
        model.train()
    else:
        model = model_class.load(model_file_name)

    if args.classify:
        for bug in bugzilla.get_bugs():
            print('https://bugzilla.mozilla.org/show_bug.cgi?id={} - {}'.format(bug['id'], bug['summary']))
            c = model.classify(bug)
            if c == 1:
                print('Positive!')
            else:
                print('Negative!')
            input()
