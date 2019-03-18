# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import itertools

import xgboost
from imblearn.over_sampling import BorderlineSMOTE
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features
from bugbug import bugzilla
from bugbug import labels
from bugbug.model import Model


class BugModel(Model):
    def __init__(self, lemmatization=False):
        Model.__init__(self, lemmatization)

        self.sampler = BorderlineSMOTE(random_state=0)

        feature_extractors = [
            bug_features.has_str(),
            bug_features.severity(),
            # Ignore keywords that would make the ML completely skewed
            # (we are going to use them as 100% rules in the evaluation phase).
            bug_features.keywords({'regression', 'talos-regression', 'feature'}),
            bug_features.is_coverity_issue(),
            bug_features.has_crash_signature(),
            bug_features.has_url(),
            bug_features.has_w3c_url(),
            bug_features.has_github_url(),
            bug_features.whiteboard(),
            bug_features.patches(),
            bug_features.landings(),
            bug_features.title(),
            bug_features.blocked_bugs_number(),
            bug_features.ever_affected(),
            bug_features.affected_then_unaffected(),
            bug_features.product(),
            bug_features.component(),
        ]

        cleanup_functions = [
            bug_features.cleanup_url,
            bug_features.cleanup_fileref,
            bug_features.cleanup_synonyms,
        ]

        self.extraction_pipeline = Pipeline([
            ('bug_extractor', bug_features.BugExtractor(feature_extractors, cleanup_functions)),
            ('union', ColumnTransformer([
                ('data', DictVectorizer(), 'data'),

                ('title', self.text_vectorizer(min_df=0.001), 'title'),

                ('first_comment', self.text_vectorizer(min_df=0.001), 'first_comment'),

                ('comments', self.text_vectorizer(min_df=0.001), 'comments'),
            ])),
        ])

        self.clf = xgboost.XGBClassifier(n_jobs=16)
        self.clf.set_params(predictor='cpu_predictor')

    def get_bugbug_labels(self, kind='bug'):
        assert kind in ['bug', 'regression', 'defect_feature_task']

        classes = {}

        for bug_id, category in labels.get_labels('bug_nobug'):
            assert category in ['True', 'False'], f'unexpected category {category}'
            if kind == 'bug':
                classes[int(bug_id)] = 1 if category == 'True' else 0
            elif kind == 'regression':
                if category == 'False':
                    classes[int(bug_id)] = 0
            elif kind == 'defect_feature_task':
                if category == 'True':
                    classes[int(bug_id)] = 'd'

        for bug_id, category in labels.get_labels('regression_bug_nobug'):
            assert category in ['nobug', 'bug_unknown_regression', 'bug_no_regression', 'regression'], f'unexpected category {category}'
            if kind == 'bug':
                classes[int(bug_id)] = 1 if category != 'nobug' else 0
            elif kind == 'regression':
                if category == 'bug_unknown_regression':
                    continue

                classes[int(bug_id)] = 1 if category == 'regression' else 0
            elif kind == 'defect_feature_task':
                if category != 'nobug':
                    classes[int(bug_id)] = 'd'

        defect_feature_task_e = {bug_id: category for bug_id, category in labels.get_labels('defect_feature_task_e')}
        defect_feature_task_p = {bug_id: category for bug_id, category in labels.get_labels('defect_feature_task_p')}
        defect_feature_task_s = {bug_id: category for bug_id, category in labels.get_labels('defect_feature_task_s')}

        defect_feature_task_common = ((bug_id, category) for bug_id, category in defect_feature_task_p.items() if (bug_id not in defect_feature_task_e or defect_feature_task_e[bug_id] == defect_feature_task_p[bug_id]) and (bug_id not in defect_feature_task_s or defect_feature_task_s[bug_id] == defect_feature_task_p[bug_id]))

        for bug_id, category in itertools.chain(labels.get_labels('defect_feature_task'), defect_feature_task_common):
            assert category in ['d', 'e', 't']
            if kind == 'bug':
                classes[int(bug_id)] = 1 if category == 'd' else 0
            elif kind == 'regression':
                if category in ['e', 't']:
                    classes[int(bug_id)] = 0
            elif kind == 'defect_feature_task':
                classes[int(bug_id)] = category

        # Augment labes by using bugs marked as 'regression' or 'feature', as they are basically labelled.
        bug_ids = set()
        for bug in bugzilla.get_bugs():
            bug_id = int(bug['id'])

            bug_ids.add(bug_id)

            if bug_id in classes:
                continue

            if any(keyword in bug['keywords'] for keyword in ['regression', 'talos-regression']) or ('cf_has_regression_range' in bug and bug['cf_has_regression_range'] == 'yes'):
                if kind in ['bug', 'regression']:
                    classes[bug_id] = 1
                else:
                    classes[bug_id] = 'd'
            elif any(keyword in bug['keywords'] for keyword in ['feature']):
                if kind in ['bug', 'regression']:
                    classes[bug_id] = 0
                else:
                    classes[bug_id] = 'e'
            elif kind == 'regression':
                for history in bug['history']:
                    for change in history['changes']:
                        if change['field_name'] == 'keywords':
                            if 'regression' in change['removed'].split(','):
                                classes[bug_id] = 0
                            elif 'regression' in change['added'].split(','):
                                classes[bug_id] = 1

        # Remove labels which belong to bugs for which we have no data.
        return {bug_id: label for bug_id, label in classes.items() if bug_id in bug_ids}

    def get_labels(self):
        return self.get_bugbug_labels('bug')

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps['union'].get_feature_names()

    def overwrite_classes(self, bugs, classes, probabilities):
        for i, bug in enumerate(bugs):
            if any(keyword in bug['keywords'] for keyword in ['regression', 'talos-regression']) or ('cf_has_regression_range' in bug and bug['cf_has_regression_range'] == 'yes'):
                classes[i] = 1 if not probabilities else [0., 1.]
            elif 'feature' in bug['keywords']:
                classes[i] = 0 if not probabilities else [1., 0.]

        return classes
