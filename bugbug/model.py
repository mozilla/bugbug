# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import numpy as np
import shap
from imblearn.metrics import classification_report_imbalanced
from imblearn.under_sampling import RandomUnderSampler
from sklearn import metrics
from sklearn.externals import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import cross_val_score
from sklearn.model_selection import train_test_split

from bugbug import bugzilla
from bugbug.nlp import SpacyVectorizer


class Model():
    def __init__(self, lemmatization=False):
        if lemmatization:
            self.text_vectorizer = SpacyVectorizer
        else:
            self.text_vectorizer = TfidfVectorizer

        self.undersampling_enabled = True
        self.cross_validation_enabled = True

    def get_feature_names(self):
        return []

    def train(self):
        classes = self.get_labels()
        class_names = sorted(list(set(classes.values())), reverse=True)

        # Get bugs, filtering out those for which we have no labels.
        def bugs():
            return (bug for bug in bugzilla.get_bugs() if bug['id'] in classes)

        # Calculate labels.
        y = np.array([classes[bug['id']] for bug in bugs()])

        # Extract features from the bugs.
        X = self.extraction_pipeline.fit_transform(bugs())

        print(f'X: {X.shape}, y: {y.shape}')

        # Split dataset in training and test.
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.1, random_state=0)

        if self.undersampling_enabled:
            # Under-sample the majority classes, as the datasets are imbalanced.
            X_train, y_train = RandomUnderSampler(random_state=0).fit_sample(X_train, y_train)

        print(f'X_train: {X_train.shape}, y_train: {y_train.shape}')
        print(f'X_test: {X_test.shape}, y_test: {y_test.shape}')

        # Use k-fold cross validation to evaluate results.
        if self.cross_validation_enabled:
            scores = cross_val_score(self.clf, X_train, y_train, cv=5)
            print(f'CV Accuracy: f{scores.mean()} (+/- {scores.std() * 2})')

        # Evaluate results on the test set.
        self.clf.fit(X_train, y_train)

        feature_names = self.get_feature_names()
        if len(feature_names):
            explainer = shap.TreeExplainer(self.clf)
            shap_values = explainer.shap_values(X_train)

            print('Feature ranking (top 20 features):')
            # Calculate the values that represent the fraction of the model output variability attributable
            # to each feature across the whole dataset.
            shap_sums = np.abs(shap_values).sum(0)
            rel_shap_sums = shap_sums / shap_sums.sum()
            indices = np.argsort(rel_shap_sums)[::-1][:20]
            for i, index in enumerate(indices):
                print(f'{i + 1}. \'{feature_names[index]}\' ({rel_shap_sums[index]})')

        y_pred = self.clf.predict(X_test)

        print(f'No confidence threshold - {len(y_test)} classified')
        print(metrics.confusion_matrix(y_test, y_pred, labels=class_names))
        print(classification_report_imbalanced(y_test, y_pred, labels=class_names))

        # Evaluate results on the test set for some confidence thresholds.
        for confidence_threshold in [0.6, 0.7, 0.8, 0.9]:
            y_pred_probas = self.clf.predict_proba(X_test)

            y_test_filter = []
            y_pred_filter = []
            for i in range(0, len(y_test)):
                argmax = np.argmax(y_pred_probas[i])
                if y_pred_probas[i][argmax] < confidence_threshold:
                    continue

                y_test_filter.append(y_test[i])
                y_pred_filter.append(argmax)

            y_pred_filter = self.clf._le.inverse_transform(y_pred_filter)

            print(f'\nConfidence threshold > {confidence_threshold} - {len(y_test_filter)} classified')
            print(metrics.confusion_matrix(y_test_filter, y_pred_filter, labels=class_names))
            print(classification_report_imbalanced(y_test_filter, y_pred_filter, labels=class_names))

        joblib.dump(self.clf, self.__class__.__name__.lower())

    @staticmethod
    def load(model_file_name):
        return joblib.load(model_file_name)

    def overwrite_classes(self, bugs, classes, probabilities):
        return classes

    def classify(self, bugs, probabilities=False, importances=False):
        assert bugs is not None
        assert self.extraction_pipeline is not None and self.clf is not None, 'The module needs to be initialized first'

        if not isinstance(bugs, list):
            bugs = [bugs]

        assert isinstance(bugs[0], dict)

        X = self.extraction_pipeline.transform(bugs)
        if probabilities:
            classes = self.clf.predict_proba(X)
        else:
            classes = self.clf.predict(X)

        classes = self.overwrite_classes(bugs, classes, probabilities)

        if importances:
            explainer = shap.TreeExplainer(self.clf)
            shap_values = explainer.shap_values(X)

            shap_sums = shap_values.sum(0)
            abs_shap_sums = np.abs(shap_sums)
            rel_shap_sums = abs_shap_sums / abs_shap_sums.sum()
            indices = np.argsort(abs_shap_sums)[::-1]
            importances = [(index, shap_sums[index] > 0, rel_shap_sums[index]) for index in indices]

            return classes, importances

        return classes
