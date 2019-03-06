# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from collections import Counter

import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features
from bugbug import bugzilla
from bugbug.model import Model


class AssigneeModel(Model):
    def __init__(self, lemmatization=False):
        Model.__init__(self, lemmatization)

        self.cross_validation_enabled = False
        self.calculate_importance = False

        feature_extractors = [
            bug_features.has_str(),
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

        cleanup_functions = [
            bug_features.cleanup_fileref,
            bug_features.cleanup_url,
            bug_features.cleanup_synonyms,
        ]

        self.extraction_pipeline = Pipeline([
            ('bug_extractor', bug_features.BugExtractor(feature_extractors, cleanup_functions, rollback=True)),
            ('union', ColumnTransformer([
                ('data', DictVectorizer(), 'data'),

                ('title', self.text_vectorizer(min_df=0.0001), 'title'),

                ('comments', self.text_vectorizer(min_df=0.0001), 'comments'),
            ])),
        ])

        self.clf = xgboost.XGBClassifier(n_jobs=16)
        self.clf.set_params(predictor='cpu_predictor')

    def get_labels(self):
        classes = {}
        addresses_to_exclude = [
            'nobody@bugzilla.org',
            'nobody@example.com',
            'nobody@fedoraproject.org',
            'nobody@mozilla.org',
            'nobody@msg1.fake',
            'nobody@nss.bugs',
            'nobody@t4b.me'
        ]
        for bug_data in bugzilla.get_bugs():
            if bug_data['assigned_to_detail']['email'] in addresses_to_exclude:
                continue

            bug_id = int(bug_data['id'])
            classes[bug_id] = bug_data['assigned_to_detail']['email']

        component_counts = Counter(classes.values()).most_common()
        top_components = set(component for component, count in component_counts)

        print(f'{len(top_components)} components')
        for component, count in component_counts:
            print(f'{component}: {count}')

        return {bug_id: component for bug_id, component in classes.items() if component in top_components}

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps['union'].get_feature_names()
