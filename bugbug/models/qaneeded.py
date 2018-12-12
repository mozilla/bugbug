# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import xgboost
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import FeatureUnion
from sklearn.pipeline import Pipeline

from bugbug import bug_features
from bugbug import labels
from bugbug.model import Model
from bugbug.utils import DictSelector


class QANeededModel(Model):
    def __init__(self, lemmatization=False):
        Model.__init__(self, lemmatization)

        self.classes = labels.get_qa_needed_labels()

        feature_extractors = [
            bug_features.has_str(),
            bug_features.has_regression_range(),
            bug_features.severity(),
            bug_features.keywords(set(['qawanted'])),
            bug_features.is_coverity_issue(),
            bug_features.has_crash_signature(),
            bug_features.has_url(),
            bug_features.has_w3c_url(),
            bug_features.has_github_url(),
            bug_features.whiteboard(),
            bug_features.patches(),
            bug_features.landings(),
            bug_features.title(),
            bug_features.comments(),
        ]

        self.extraction_pipeline = Pipeline([
            ('bug_extractor', bug_features.BugExtractor(feature_extractors)),
            ('union', FeatureUnion(
                transformer_list=[
                    ('data', Pipeline([
                        ('selector', DictSelector(key='data')),
                        ('vect', DictVectorizer()),
                    ])),

                    ('title', Pipeline([
                        ('selector', DictSelector(key='title')),
                        ('tfidf', self.text_vectorizer(stop_words='english')),
                    ])),

                    ('comments', Pipeline([
                        ('selector', DictSelector(key='comments')),
                        ('tfidf', self.text_vectorizer(stop_words='english')),
                    ])),
                ],
            )),
        ])

        self.clf = xgboost.XGBClassifier(n_jobs=16)
