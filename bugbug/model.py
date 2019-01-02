# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import numpy as np
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

        # Get bugs.
        def bugs_all():
            return bugzilla.get_bugs()

        # Filter out bugs for which we have no labels.
        def bugs():
            return (bug for bug in bugs_all() if bug['id'] in classes)

        # Calculate labels.
        y = np.array([1 if classes[bug['id']] else 0 for bug in bugs()])

        # Extract features from the bugs.
        X = self.extraction_pipeline.fit_transform(bugs())

        print(X.shape, y.shape)

        # Split dataset in training and test.
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.1, random_state=0)

        if self.undersampling_enabled:
            # Under-sample the majority classes, as the datasets are imbalanced.
            X_train, y_train = RandomUnderSampler(random_state=0).fit_sample(X_train, y_train)

        print(X_train.shape, y_train.shape)
        print(X_test.shape, y_test.shape)

        # Use k-fold cross validation to evaluate results.
        if self.cross_validation_enabled:
            scores = cross_val_score(self.clf, X_train, y_train, cv=5)
            print('CV Accuracy: %0.2f (+/- %0.2f)' % (scores.mean(), scores.std() * 2))

        # Evaluate results on the test set.
        self.clf.fit(X_train, y_train)

        feature_names = self.get_feature_names()
        if len(feature_names):
            print('Feature ranking (top 20 features):')
            indices = np.argsort(self.clf.feature_importances_)[::-1][:20]
            for i in range(len(indices)):
                print('{}. \'{}\' ({})'.format(i + 1, feature_names[indices[i]], self.clf.feature_importances_[indices[i]]))

        y_pred = self.clf.predict(X_test)

        print(metrics.confusion_matrix(y_test, y_pred, labels=[1, 0]))
        print(classification_report_imbalanced(y_test, y_pred, labels=[1, 0]))

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

            print('\nConfidence threshold > {} - {} classified'.format(confidence_threshold, len(y_test_filter)))
            print(metrics.confusion_matrix(y_test_filter, y_pred_filter, labels=[1, 0]))
            print(classification_report_imbalanced(y_test_filter, y_pred_filter, labels=[1, 0]))

        joblib.dump(self, '{}'.format(self.__class__.__name__.lower()))

    @staticmethod
    def load(model_file_name):
        return joblib.load(model_file_name)

    def overwrite_classes(self, bugs, classes, probabilities):
        return classes

    def classify(self, bugs, probabilities=False):
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

        return self.overwrite_classes(bugs, classes, probabilities)
