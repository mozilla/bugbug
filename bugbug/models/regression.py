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


class RegressionModel(Model):
    def __init__(self, lemmatization=False):
        Model.__init__(self, lemmatization)

        self.classes = labels.get_bugbug_labels(kind='regression', augmentation=True)

        feature_extractors = [
            bug_features.has_str(),
            bug_features.severity(),
            # Ignore keywords that would make the ML completely skewed
            # (we are going to use them as 100% rules in the evaluation phase).
            bug_features.keywords(set(['regression', 'talos-regression', 'feature'])),
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
        self.clf.set_params(tree_method='exact', predictor='cpu_predictor')

    def get_feature_names(self):
        return ['data_' + name for name in self.data_vectorizer.get_feature_names()] +\
               ['title_' + name for name in self.title_vectorizer.get_feature_names()] +\
               ['comments_' + name for name in self.comments_vectorizer.get_feature_names()]

    def overwrite_classes(self, bugs, classes, probabilities):
        for i, bug in enumerate(bugs):
            if any(keyword in bug['keywords'] for keyword in ['regression', 'talos-regression']) or ('cf_has_regression_range' in bug and bug['cf_has_regression_range'] == 'yes'):
                classes[i] = 1 if not probabilities else [1., 0.]
            elif 'feature' in bug['keywords']:
                classes[i] = 0 if not probabilities else [0., 1.]

        return classes
