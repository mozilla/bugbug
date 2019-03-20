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


class ComponentModel(Model):
    PRODUCTS = {
        'Core', 'External Software Affecting Firefox', 'DevTools', 'Firefox for Android', 'Firefox', 'Toolkit',
        'WebExtensions', 'Firefox Build System',
    }

    CONFLATED_COMPONENTS = [
        'Core::Audio/Video', 'Core::DOM', 'Core::Graphics', 'Core::IPC', 'Core::JavaScript', 'Core::Layout',
        'Core::Networking', 'Core::Print', 'Core::WebRTC', 'Firefox::Activity Streams', 'Toolkit::Password Manager',
        'DevTools', 'External Software Affecting Firefox', 'WebExtensions', 'Firefox Build System',
    ]

    CONFLATED_COMPONENTS_MAPPING = {
        'Core::DOM': 'Core::DOM: Core & HTML',
        'Core::JavaScript': 'Core::JavaScript Engine',
        'Core::Print': 'Core::Printing: Output',
        'Firefox::Activity Streams': 'Firefox::Activity Streams: Newtab',
        'DevTools': 'DevTools::General',
        'External Software Affecting Firefox': 'External Software Affecting Firefox::Other',
        'WebExtensions': 'WebExtensions::Untriaged',
        'Firefox Build System': 'Firefox Build System::General',
    }

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

        self.CONFLATED_COMPONENTS_INVERSE_MAPPING = {v: k for k, v in self.CONFLATED_COMPONENTS_MAPPING.items()}

    def filter_component(self, product, component):
        full_comp = f'{product}::{component}'

        if full_comp in self.CONFLATED_COMPONENTS_INVERSE_MAPPING:
            return self.CONFLATED_COMPONENTS_INVERSE_MAPPING[full_comp]

        if (product, component) in self.meaningful_product_components:
            return full_comp

        for conflated_component in self.CONFLATED_COMPONENTS:
            if full_comp.startswith(conflated_component):
                return conflated_component

        return None

    def get_labels(self):
        product_components = {}
        for bug_data in bugzilla.get_bugs():
            product_components[bug_data['id']] = (bug_data['product'], bug_data['component'])

        def is_meaningful(product, component):
            return product in self.PRODUCTS and component not in ['General', 'Untriaged']

        product_component_counts = Counter(((product, component) for product, component in product_components.values() if is_meaningful(product, component))).most_common()

        max_count = product_component_counts[0][1]
        threshold = max_count / 100

        self.meaningful_product_components = set(product_component for product_component, count in product_component_counts if count > threshold)

        classes = {}
        for bug_id, (product, component) in product_components.items():
            component = self.filter_component(product, component)

            if component:
                classes[bug_id] = component

        component_counts = Counter(classes.values()).most_common()
        top_components = set(component for component, count in component_counts)

        print(f'{len(top_components)} components')
        for component, count in component_counts:
            print(f'{component}: {count}')

        # Assert there is at least one bug for each conflated component.
        for conflated_component in self.CONFLATED_COMPONENTS:
            assert any(conflated_component == component for component, count in component_counts), f'There should be at least one bug matching {conflated_component}*'

        # Assert there is at least one bug for each component the conflated components are mapped to.
        for conflated_component_mapping in self.CONFLATED_COMPONENTS_MAPPING.values():
            assert any(conflated_component_mapping == f'{product}::{component}' for product, component in product_components.values()), f'There should be at least one bug in {conflated_component_mapping}'

        # Assert all conflated components are either in conflated_components_mapping or exist as components.
        for conflated_component in self.CONFLATED_COMPONENTS:
            assert conflated_component in self.CONFLATED_COMPONENTS_MAPPING or \
                any(conflated_component == f'{product}::{component}' for product, component in product_components.values()), f'It should be possible to map {conflated_component}'

        return {bug_id: component for bug_id, component in classes.items() if component in top_components}

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps['union'].get_feature_names()
