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
from sklearn.model_selection import cross_validate, train_test_split

from bugbug import bugzilla, repository
from bugbug.nlp import SpacyVectorizer


class Model:
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
        # Stack the importance, indices and shap_sums in a 2D array
        top_features = np.column_stack((top_features, top_feature_indices, is_positive))
        # Sort the array (in decreasing order of importance values)
        top_features = top_features[top_features[:, 0].argsort()][::-1]

        return top_features

    def train(self, importance_cutoff=0.15):
        classes, class_names = self.get_labels()
        class_names = sorted(list(class_names), reverse=True)

        # Get items, filtering out those for which we have no labels.
        def trainable_items_gen():
            return (item for item in self.items_gen() if self.get_id(item) in classes)

        # Calculate labels.
        y = np.array([classes[self.get_id(item)] for item in trainable_items_gen()])

        # Extract features from the items.
        X = self.extraction_pipeline.fit_transform(trainable_items_gen())

        print(f"X: {X.shape}, y: {y.shape}")

        # Split dataset in training and test.
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.1, random_state=0
        )
        if self.sampler is not None:
            pipeline = make_pipeline(self.sampler, self.clf)
        else:
            pipeline = self.clf

        # Use k-fold cross validation to evaluate results.
        if self.cross_validation_enabled:
            scorings = ["accuracy"]
            if len(class_names) == 2:
                scorings += ["precision", "recall"]

            scores = cross_validate(pipeline, X_train, y_train, scoring=scorings, cv=5)

            print("Cross Validation scores:")
            for scoring in scorings:
                score = scores[f"test_{scoring}"]
                print(
                    f"{scoring.capitalize()}: f{score.mean()} (+/- {score.std() * 2})"
                )

        # Training on the resampled dataset if sampler is provided.
        if self.sampler is not None:
            X_train, y_train = self.sampler.fit_resample(X_train, y_train)

        print(f"X_train: {X_train.shape}, y_train: {y_train.shape}")
        print(f"X_test: {X_test.shape}, y_test: {y_test.shape}")

        self.clf.fit(X_train, y_train)

        # Evaluate results on the test set.
        feature_names = self.get_feature_names()
        if self.calculate_importance and len(feature_names):
            explainer = shap.TreeExplainer(self.clf)
            shap_values = explainer.shap_values(X_train)

            # TODO: Actually implement feature importance visualization for multiclass problems.
            if isinstance(shap_values, list):
                shap_values = np.sum(np.abs(shap_values), axis=0)

            important_features = self.get_important_features(
                importance_cutoff, shap_values
            )

            print(f"\nTop {len(important_features)} Features:")
            for i, [importance, index, is_positive] in enumerate(important_features):
                print(
                    f'{i + 1}. \'{feature_names[int(index)]}\' ({"+" if (is_positive) else "-"}{importance})'
                )

        y_pred = self.clf.predict(X_test)

        print(f"No confidence threshold - {len(y_test)} classified")
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

            print(
                f"\nConfidence threshold > {confidence_threshold} - {len(y_test_filter)} classified"
            )
            print(
                metrics.confusion_matrix(
                    y_test_filter, y_pred_filter, labels=class_names
                )
            )
            print(
                classification_report_imbalanced(
                    y_test_filter, y_pred_filter, labels=class_names
                )
            )

        joblib.dump(self, self.__class__.__name__.lower())

    @staticmethod
    def load(model_file_name):
        return joblib.load(model_file_name)

    def overwrite_classes(self, items, classes, probabilities):
        return classes

    def classify(
        self, items, probabilities=False, importances=False, importance_cutoff=0.15
    ):
        assert items is not None
        assert (
            self.extraction_pipeline is not None and self.clf is not None
        ), "The module needs to be initialized first"

        if not isinstance(items, list):
            items = [items]

        assert isinstance(items[0], dict)

        X = self.extraction_pipeline.transform(items)
        if probabilities:
            classes = self.clf.predict_proba(X)
        else:
            classes = self.clf.predict(X)

        classes = self.overwrite_classes(items, classes, probabilities)

        if importances:
            explainer = shap.TreeExplainer(self.clf)
            shap_values = explainer.shap_values(X)

            # TODO: Actually implement feature importance visualization for multiclass problems.
            if isinstance(shap_values, list):
                shap_values = np.sum(np.abs(shap_values), axis=0)

            importances = self.get_important_features(importance_cutoff, shap_values)

            return classes, importances

        return classes

    def check(self):
        """ Subclasses can implement their own check, the base model doesn't
        check anything at the moment
        """
        return True


class BugModel(Model):
    def get_id(self, bug):
        return bug["id"]

    def items_gen(self):
        return (bug for bug in bugzilla.get_bugs())


class CommitModel(Model):
    def get_id(self, commit):
        return commit["node"]

    def items_gen(self):
        return (commit for commit in repository.get_commits())
