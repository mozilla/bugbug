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
from bugbug import labels
from bugbug.model import Model
from bugbug.utils import DictSelector


class BugModel(Model):
    def __init__(self, lemmatization=False):
        Model.__init__(self, lemmatization)

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
        self.clf.set_params(predictor='cpu_predictor')

    def get_bugbug_labels(self, kind='bug'):
        assert kind in ['bug', 'regression']

        classes = {}

        for bug_id, category in labels.get_labels('bug_nobug'):
            assert category in ['True', 'False'], 'unexpected category {}'.format(category)
            if kind == 'bug':
                classes[int(bug_id)] = True if category == 'True' else False
            elif kind == 'regression':
                if category == 'False':
                    classes[int(bug_id)] = False

        for bug_id, category in labels.get_labels('regression_bug_nobug'):
            assert category in ['nobug', 'bug_unknown_regression', 'bug_no_regression', 'regression'], 'unexpected category {}'.format(category)
            if kind == 'bug':
                classes[int(bug_id)] = True if category != 'nobug' else False
            elif kind == 'regression':
                if category == 'bug_unknown_regression':
                    continue

                classes[int(bug_id)] = True if category == 'regression' else False

        # Augment labes by using bugs marked as 'regression' or 'feature', as they are basically labelled.
        bug_ids = set()
        for bug in bugzilla.get_bugs():
            bug_id = int(bug['id'])

            bug_ids.add(bug_id)

            if bug_id in classes:
                continue

            if any(keyword in bug['keywords'] for keyword in ['regression', 'talos-regression']) or ('cf_has_regression_range' in bug and bug['cf_has_regression_range'] == 'yes'):
                classes[bug_id] = True
            elif any(keyword in bug['keywords'] for keyword in ['feature']):
                classes[bug_id] = False
            elif kind == 'regression':
                for history in bug['history']:
                    for change in history['changes']:
                        if change['field_name'] == 'keywords' and change['removed'] == 'regression':
                            classes[bug_id] = False

        # Remove labels which belong to bugs for which we have no data.
        return {bug_id: label for bug_id, label in classes.items() if bug_id in bug_ids}

    def get_labels(self):
        return self.get_bugbug_labels('bug')

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
