# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import numpy as np
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

    def train(self):
        # Get bugs.
        def bugs_all():
            return bugzilla.get_bugs()

        # Filter out bugs for which we have no labels.
        def bugs():
            return (bug for bug in bugs_all() if bug['id'] in self.classes)

        # Calculate labels.
        y = np.array([1 if self.classes[bug['id']] else 0 for bug in bugs()])

        # Extract features from the bugs.
        X = self.extraction_pipeline.fit_transform(bugs())

        # Under-sample the 'bug' class, as there are too many compared to 'feature'.
        X, y = RandomUnderSampler(random_state=0).fit_sample(X, y)

        # Split dataset in training and test.
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.1, random_state=0)
        print(X_train.shape, y_train.shape)
        print(X_test.shape, y_test.shape)

        # Use k-fold cross validation to evaluate results.
        scores = cross_val_score(self.clf, X_train, y_train, cv=5)
        print('CV Accuracy: %0.2f (+/- %0.2f)' % (scores.mean(), scores.std() * 2))

        # Evaluate results on the test set.
        self.clf.fit(X_train, y_train)
        y_pred = self.clf.predict(X_test)
        print('Accuracy: {}'.format(metrics.accuracy_score(y_test, y_pred)))
        print('Precision: {}'.format(metrics.precision_score(y_test, y_pred)))
        print('Recall: {}'.format(metrics.recall_score(y_test, y_pred)))
        print(metrics.confusion_matrix(y_test, y_pred))

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

        if isinstance(bugs[0], int) or isinstance(bugs[0], str):
            bugs = list(bugzilla.download_bugs([int(i) for i in bugs]))

        X = self.extraction_pipeline.transform(bugs)
        if probabilities:
            classes = self.clf.predict_proba(X)
        else:
            classes = self.clf.predict(X)

        return self.overwrite_classes(bugs, classes, probabilities)
