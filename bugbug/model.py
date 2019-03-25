# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import numpy as np
import shap
from imblearn.metrics import classification_report_imbalanced
from imblearn.pipeline import make_pipeline
from sklearn import metrics
from sklearn.externals import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import cross_validate
from sklearn.model_selection import train_test_split

from bugbug import bugzilla
from bugbug.nlp import SpacyVectorizer


class Model():
    def __init__(self, lemmatization=False):
        if lemmatization:
            self.text_vectorizer = SpacyVectorizer
        else:
            self.text_vectorizer = TfidfVectorizer

        self.cross_validation_enabled = True
        self.sampler = None

        self.calculate_importance = True

    @property
    def le(self):
        """Classifier agnostic getter for the label encoder property"""
        try:
            return self.clf._le
        except AttributeError:
            return self.clf.le_

    def get_feature_names(self):
        return []

    def get_important_features(self, cutoff, shap_values):
        # shap_values is a list for models with vector output
        if isinstance(shap_values, list):
            shap_values = np.array(shap_values)
            class_shap_values = []

            for matrix in shap_values:
                shap_sum = (np.abs(matrix)).sum(0)
                class_shap_values.append(shap_sum)

            # Importance of features (sum of all classes)
            feature_shap_vals = np.array(class_shap_values).sum(0)
            sorted_feature_indices = np.argsort(feature_shap_vals)[::-1]
            # features: matrix of [features x classes] sorted by importance of features
            features = (np.array(class_shap_values).T)[sorted_feature_indices]

            cut_off_value = cutoff * np.amax(feature_shap_vals)
            # Gets the number of features that pass the cut off value
            cut_off_len = len(np.where(feature_shap_vals >= cut_off_value)[0])

            # Stack the original indices and feature importance along with the features
            # above the cut off length
            top_features = np.column_stack((
                sorted_feature_indices[:cut_off_len],
                feature_shap_vals[sorted_feature_indices][:cut_off_len],
                features[:cut_off_len]
            ))

            return top_features

        else:
            # Calculate the values that represent the fraction of the model output variability attributable
            # to each feature across the whole dataset.
            shap_sums = shap_values.sum(0)
            abs_shap_sums = np.abs(shap_values).sum(0)
            rel_shap_sums = abs_shap_sums / abs_shap_sums.sum()

            cut_off_value = cutoff * np.amax(rel_shap_sums)

            # Get indices of features that pass the cut off value
            top_feature_indices = np.where(rel_shap_sums >= cut_off_value)[0]
            # Get the importance values of the top features from their indices
            top_features = np.take(rel_shap_sums, top_feature_indices)
            # Gets the sign of the importance from shap_sums as boolean
            is_positive = (np.take(shap_sums, top_feature_indices)) >= 0
            # Stack the importance, indices and shap_sums a 2D array
            top_features = np.column_stack((top_features, top_feature_indices, is_positive))
            # Sort the array (in decreasing order of importance values)
            top_features = top_features[top_features[:, 0].argsort()][::-1]

            return top_features

    def train(self, importance_cutoff=0.15):
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
        if self.sampler is not None:
            pipeline = make_pipeline(self.sampler, self.clf)
        else:
            pipeline = self.clf

        # Use k-fold cross validation to evaluate results.
        if self.cross_validation_enabled:
            scorings = ['accuracy']
            if len(class_names) == 2:
                scorings += ['precision', 'recall']

            scores = cross_validate(pipeline, X_train, y_train, scoring=scorings, cv=5)

            print('Cross Validation scores:')
            for scoring in scorings:
                score = scores[f'test_{scoring}']
                print(f'{scoring.capitalize()}: f{score.mean()} (+/- {score.std() * 2})')

        # Training on the resampled dataset if sampler is provided.
        if self.sampler is not None:
            X_train, y_train = self.sampler.fit_resample(X_train, y_train)

        print(f'X_train: {X_train.shape}, y_train: {y_train.shape}')
        print(f'X_test: {X_test.shape}, y_test: {y_test.shape}')

        self.clf.fit(X_train, y_train)

        # Evaluate results on the test set.
        feature_names = self.get_feature_names()
        if self.calculate_importance and len(feature_names):
            explainer = shap.TreeExplainer(self.clf)
            shap_values = explainer.shap_values(X_train)

            important_features = self.get_important_features(importance_cutoff, shap_values)
            if isinstance(shap_values, list):
                # Number of classes to be printed
                no_of_classes = 6
                print('\n Top Features \n')
                for i, (feature_index, shap_sum, *feature) in enumerate(important_features):
                    class_indices = np.argsort(feature)[::-1]
                    print(f'{str(i+1).zfill(2)}. {feature_names[int(feature_index)]} (Importance: {shap_sum:.5f})')

                    for index in class_indices[:no_of_classes]:
                        class_no = f'Class {str(index).zfill(3)}    '
                        print(class_no, end='')
                    print()
                    for index in class_indices[:no_of_classes]:
                        imp_val = f'{feature[index]:.7f}'
                        # Horizontally align the importance value
                        print(imp_val, end=' ' * (len(class_no) - len(imp_val)))
                    print('\n\n')
                print()
        else:
            print(f'\nTop {len(important_features)} Features:')
            for i, [importance, index, is_positive] in enumerate(important_features):
                print(f'{i + 1}. \'{feature_names[int(index)]}\' ({"+" if (is_positive) else "-"}{importance})')

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

            y_pred_filter = self.le.inverse_transform(y_pred_filter)

            print(f'\nConfidence threshold > {confidence_threshold} - {len(y_test_filter)} classified')
            print(metrics.confusion_matrix(y_test_filter, y_pred_filter, labels=class_names))
            print(classification_report_imbalanced(y_test_filter, y_pred_filter, labels=class_names))

        joblib.dump(self, self.__class__.__name__.lower())

    @staticmethod
    def load(model_file_name):
        return joblib.load(model_file_name)

    def overwrite_classes(self, bugs, classes, probabilities):
        return classes

    def classify(self, bugs, probabilities=False, importances=False, importance_cutoff=0.15):
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

            importances = self.get_important_features(importance_cutoff, shap_values)

            return classes, importances

        return classes
