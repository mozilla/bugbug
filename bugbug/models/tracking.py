# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import xgboost
from imblearn.under_sampling import InstanceHardnessThreshold
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features
from bugbug import bugzilla
from bugbug import labels
from bugbug.model import Model


class TrackingModel(Model):
    def __init__(self, lemmatization=False):
        Model.__init__(self, lemmatization)

        self.sampler = InstanceHardnessThreshold(random_state=0)

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
            bug_features.product(),
            bug_features.component(),
            bug_features.is_mozillian(),
            bug_features.bug_reporter(),
            bug_features.blocked_bugs_number(),
            bug_features.priority(),
            bug_features.has_cve_in_alias(),
            bug_features.comment_count(),
            bug_features.comment_length(),
            bug_features.reporter_experience(),
            bug_features.number_of_bug_dependencies()
        ]

        cleanup_functions = [
            bug_features.cleanup_url,
            bug_features.cleanup_fileref,
            bug_features.cleanup_hex,
            bug_features.cleanup_dll,
            bug_features.cleanup_synonyms,
            bug_features.cleanup_crash,
        ]

        self.extraction_pipeline = Pipeline([
            ('bug_extractor', bug_features.BugExtractor(feature_extractors, cleanup_functions, rollback=True, rollback_when=self.rollback)),
            ('union', ColumnTransformer([
                ('data', DictVectorizer(), 'data'),

                ('title', self.text_vectorizer(min_df=0.0001), 'title'),

                ('comments', self.text_vectorizer(min_df=0.0001), 'comments'),
            ])),
        ])

        self.clf = xgboost.XGBClassifier(n_jobs=16)
        self.clf.set_params(predictor='cpu_predictor')

    def rollback(self, change):
        return change['field_name'].startswith('cf_tracking_firefox')

    def get_labels(self):
        classes = {}

        for bug_id, category in labels.get_labels('tracking'):
            assert category in ['True', 'False'], f'unexpected category {category}'
            classes[int(bug_id)] = 1 if category == 'True' else 0

        for bug_data in bugzilla.get_bugs():
            bug_id = int(bug_data['id'])

            for entry in bug_data['history']:
                for change in entry['changes']:
                    if change['field_name'].startswith('cf_tracking_firefox'):
                        if change['added'] in ['blocking', '+']:
                            classes[bug_id] = 1
                        elif change['added'] == '-':
                            classes[bug_id] = 0

            if bug_data['resolution'] in ['INVALID', 'DUPLICATE']:
                continue

            if bug_id not in classes:
                classes[bug_id] = 0

        return classes

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps['union'].get_feature_names()

    def overwrite_classes(self, bugs, classes, probabilities):
        for i, bug in enumerate(bugs):
            if bug['resolution'] in ['INVALID', 'DUPLICATE']:
                classes[i] = 0 if not probabilities else [1., 0.]

        return classes
