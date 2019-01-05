# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import xgboost
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import FeatureUnion
from sklearn.pipeline import Pipeline

from bugbug import bug_features
from bugbug import bugzilla
from bugbug.model import Model
from bugbug.utils import DictSelector


class UpliftModel(Model):
    def __init__(self, lemmatization=False):
        Model.__init__(self, lemmatization)

        feature_extractors = [
            bug_features.has_str(),
            bug_features.has_regression_range(),
            bug_features.severity(),
            bug_features.keywords(),
            bug_features.is_coverity_issue(),
            bug_features.has_crash_signature(),
            bug_features.has_url(),
            bug_features.has_w3c_url(),
            bug_features.has_github_url(),
            bug_features.whiteboard(),
            bug_features.patches(),
            bug_features.landings(),
            bug_features.title(),
        ]

        self.data_vectorizer = DictVectorizer()
        self.title_vectorizer = self.text_vectorizer(stop_words='english')
        self.comments_vectorizer = self.text_vectorizer(stop_words='english')

        self.extraction_pipeline = Pipeline([
            ('bug_extractor', bug_features.BugExtractor(feature_extractors)),
            ('union', FeatureUnion(
                transformer_list=[
                    ('data', Pipeline([
                        ('selector', DictSelector(key='data')),
                        ('vect', self.data_vectorizer),
                    ])),

                    ('title', Pipeline([
                        ('selector', DictSelector(key='title')),
                        ('tfidf', self.title_vectorizer),
                    ])),

                    ('comments', Pipeline([
                        ('selector', DictSelector(key='comments')),
                        ('tfidf', self.comments_vectorizer),
                    ])),
                ],
            )),
        ])

        self.clf = xgboost.XGBClassifier(n_jobs=16)
        self.clf.set_params(predictor='cpu_predictor')

    def get_labels(self):
        classes = {}

        for bug_data in bugzilla.get_bugs():
            bug_id = int(bug_data['id'])

            for attachment in bug_data['attachments']:
                for flag in attachment['flags']:
                    if not flag['name'].startswith('approval-mozilla-') or flag['status'] not in ['+', '-']:
                        continue

                    if flag['status'] == '+':
                        classes[bug_id] = 1
                    elif flag['status'] == '-':
                        classes[bug_id] = 0

        return classes

    def get_feature_names(self):
        return ['data_' + name for name in self.data_vectorizer.get_feature_names()] +\
               ['title_' + name for name in self.title_vectorizer.get_feature_names()] +\
               ['comments_' + name for name in self.comments_vectorizer.get_feature_names()]
