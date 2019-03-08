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
        'WebExtensions'
    }

    CONFLATED_COMPONENTS = [
        'Core::Audio/Video', 'Core::Graphics', 'Core::IPC', 'Core::JavaScript', 'Core::Layout', 'Core::Networking',
        'Core::Print', 'Core::WebRTC', 'Firefox::Activity Streams', 'Toolkit::Password Manager',
        'DevTools', 'External Software Affecting Firefox', 'WebExtensions',
    ]

    CONFLATED_COMPONENTS_MAPPING = {
        'Core::JavaScript': 'Core::JavaScript Engine',
        'Core::Print': 'Core::Printing: Output',
        'Firefox::Activity Streams': 'Firefox::Activity Streams: Newtab',
        'DevTools': 'DevTools::General',
        'External Software Affecting Firefox': 'External Software Affecting Firefox: Other',
        'WebExtensions': 'WebExtensions::Untriaged',
    }

    MEANINGFUL_COMPONENTS = [
        'Core::CSS Parsing and Computation', 'Core::Canvas: 2D', 'Core::Canvas: WebGL', 'Core::DOM',
        'Core::DOM: Animation', 'Core::DOM: CSS Object Model', 'Core::DOM: Content Processes',
        'Core::DOM: Device Interfaces', 'Core::DOM: Events', 'Core::DOM: IndexedDB',
        'Core::DOM: Push Notifications', 'Core::DOM: Security', 'Core::DOM: Service Workers',
        'Core::DOM: Web Payments', 'Core::DOM: Workers', 'Core::Disability Access APIs',
        'Core::Document Navigation', 'Core::Drag and Drop', 'Core::Editor', 'Core::Event Handling',
        'Core::Gecko Profiler', 'Core::Geolocation', 'Core::HTML: Parser', 'Core::ImageLib',
        'Core::Internationalization', 'Core::MFBT', 'Core::MathML', 'Core::Memory Allocator',
        'Core::Panning and Zooming', 'Core::Plug-ins', 'Core::Preferences: Backend', 'Core::SVG',
        'Core::Security', 'Core::Security: PSM', 'Core::Security: Process Sandboxing', 'Core::Selection',
        'Core::Spelling checker', 'Core::String', 'Core::Web Audio', 'Core::Web Painting', 'Core::Web Replay',
        'Core::Web Speech', 'Core::WebVR', 'Core::Widget', 'Core::Widget: Android', 'Core::Widget: Cocoa',
        'Core::Widget: Gtk', 'Core::Widget: Win32', 'Core::XPCOM', 'Core::XPConnect', 'Core::XUL',
        'DevTools::Accessibility Tools', 'DevTools::Animation Inspector', 'DevTools::CSS Rules Inspector',
        'DevTools::Console', 'DevTools::Debugger', 'DevTools::Font Inspector', 'DevTools::Framework',
        'DevTools::Inspector', 'DevTools::JSON Viewer', 'DevTools::Memory', 'DevTools::Netmonitor',
        'DevTools::Performance Tools (Profiler/Timeline)', 'DevTools::Responsive Design Mode',
        'DevTools::Shared Components', 'DevTools::Storage Inspector', 'DevTools::Style Editor', 'DevTools::WebIDE',
        'DevTools::about:debugging',
        'Firefox for Android::Activity Stream', 'Firefox for Android::Android Sync',
        'Firefox for Android::Audio/Video', 'Firefox for Android::Awesomescreen',
        'Firefox for Android::Firefox Accounts', 'Firefox for Android::GeckoView',
        'Firefox for Android::Keyboards and IME', 'Firefox for Android::Metrics',
        'Firefox for Android::Settings and Preferences', 'Firefox for Android::Testing',
        'Firefox for Android::Theme and Visual Design', 'Firefox for Android::Toolbar',
        'Firefox for Android::Web Apps',
        'Firefox::Address Bar', 'Firefox::Device Permissions', 'Firefox::Downloads Panel',
        'Firefox::Enterprise Policies', 'Firefox::Extension Compatibility', 'Firefox::File Handling',
        'Firefox::Installer', 'Firefox::Keyboard Navigation', 'Firefox::Menus', 'Firefox::Migration',
        'Firefox::New Tab Page', 'Firefox::Normandy Client', 'Firefox::PDF Viewer', 'Firefox::Pocket',
        'Firefox::Preferences', 'Firefox::Private Browsing', 'Firefox::Screenshots', 'Firefox::Search',
        'Firefox::Security', 'Firefox::Session Restore', 'Firefox::Shell Integration',
        'Firefox::Site Identity and Permission Panels', 'Firefox::Sync', 'Firefox::Tabbed Browser',
        'Firefox::Theme', 'Firefox::Toolbars and Customization', 'Firefox::Tours',
        'Firefox::Tracking Protection', 'Firefox::WebPayments UI',
        'Toolkit::Add-ons Manager', 'Toolkit::Application Update', 'Toolkit::Blocklist Policy Requests',
        'Toolkit::Crash Reporting', 'Toolkit::Downloads API', 'Toolkit::Find Toolbar', 'Toolkit::Form Autofill',
        'Toolkit::Form Manager', 'Toolkit::Notifications and Alerts', 'Toolkit::Performance Monitoring',
        'Toolkit::Places', 'Toolkit::Reader Mode', 'Toolkit::Safe Browsing', 'Toolkit::Startup and Profile System',
        'Toolkit::Storage', 'Toolkit::Telemetry', 'Toolkit::Themes', 'Toolkit::Video/Audio Controls',
        'Toolkit::XUL Widgets',
    ]

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

    def filter_component(self, bug_data):
        full_comp = f'{bug_data["product"]}::{bug_data["component"]}'

        if full_comp in self.MEANINGFUL_COMPONENTS:
            return full_comp

        for conflated_component in self.CONFLATED_COMPONENTS:
            if full_comp.startswith(conflated_component):
                return conflated_component

        return None

    def get_labels(self):
        classes = {}

        for bug_data in bugzilla.get_bugs():
            bug_id = int(bug_data['id'])
            component = self.filter_component(bug_data)

            if component:
                classes[bug_id] = component

        component_counts = Counter(classes.values()).most_common()
        top_components = set(component for component, count in component_counts)

        print(f'{len(top_components)} components')
        for component, count in component_counts:
            print(f'{component}: {count}')

        return {bug_id: component for bug_id, component in classes.items() if component in top_components}

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps['union'].get_feature_names()
