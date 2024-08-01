# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import pickle
from collections import defaultdict
from os import makedirs, path
from typing import Any

import matplotlib
import numpy as np
import shap
from imblearn.metrics import (
    classification_report_imbalanced,
    geometric_mean_score,
    make_index_balanced_accuracy,
    specificity_score,
)
from sklearn import metrics
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import precision_recall_fscore_support
from sklearn.model_selection import cross_validate, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder
from tabulate import tabulate
from xgboost import XGBModel

from bugbug import bugzilla, db, repository
from bugbug.github import Github
from bugbug.nlp import SpacyVectorizer
from bugbug.utils import split_tuple_generator, to_array

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_transformer_pipeline(pipeline: Pipeline) -> Pipeline:
    """Create a pipeline that contains only the transformers.

    This will exclude any steps that do not have a transform method, such as a
    sampler or estimator.

    Args:
        pipeline: the pipeline to extract the transformers from.

    Returns:
        a pipeline that contains only the transformers.
    """
    return Pipeline(
        [
            (name, transformer)
            for name, transformer in pipeline.steps
            if hasattr(transformer, "transform")
        ]
    )


def classification_report_imbalanced_values(
    y_true, y_pred, labels, target_names=None, sample_weight=None, digits=2, alpha=0.1
):
    """Build a classification report based on metrics used with imbalanced dataset.

    Copy of imblearn.metrics.classification_report_imbalanced to have
    access to the raw values. The code is mostly the same except the
    formatting code and generation of the report which haven removed. Copied
    from version 0.4.3. The original code is living here:
    https://github.com/scikit-learn-contrib/imbalanced-learn/blob/b861b3a8e3414c52f40a953f2e0feca5b32e7460/imblearn/metrics/_classification.py#L790
    """
    labels = np.asarray(labels)

    if target_names is None:
        target_names = [str(label) for label in labels]

    # Compute the different metrics
    # Precision/recall/f1
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, average=None, sample_weight=sample_weight
    )
    # Specificity
    specificity = specificity_score(
        y_true, y_pred, labels=labels, average=None, sample_weight=sample_weight
    )
    # Geometric mean
    geo_mean = geometric_mean_score(
        y_true, y_pred, labels=labels, average=None, sample_weight=sample_weight
    )
    # Index balanced accuracy
    iba_gmean = make_index_balanced_accuracy(alpha=alpha, squared=True)(
        geometric_mean_score
    )
    iba = iba_gmean(
        y_true, y_pred, labels=labels, average=None, sample_weight=sample_weight
    )

    result = {"targets": {}}

    for i, label in enumerate(labels):
        result["targets"][target_names[i]] = {
            "precision": precision[i],
            "recall": recall[i],
            "specificity": specificity[i],
            "f1": f1[i],
            "geo_mean": geo_mean[i],
            "iba": iba[i],
            "support": support[i],
        }

    result["average"] = {
        "precision": np.average(precision, weights=support),
        "recall": np.average(recall, weights=support),
        "specificity": np.average(specificity, weights=support),
        "f1": np.average(f1, weights=support),
        "geo_mean": np.average(geo_mean, weights=support),
        "iba": np.average(iba, weights=support),
        "support": np.sum(support),
    }

    return result


def print_labeled_confusion_matrix(confusion_matrix, labels, is_multilabel=False):
    confusion_matrix_table = confusion_matrix.tolist()

    # Don't show the Not classified row in the table output
    if "__NOT_CLASSIFIED__" in labels and not is_multilabel:
        confusion_matrix_table.pop(labels.index("__NOT_CLASSIFIED__"))

    if not is_multilabel:
        confusion_matrix_table = [confusion_matrix_table]

    for num, table in enumerate(confusion_matrix_table):
        if is_multilabel:
            print(f"label: {labels[num]}")
            table_labels = [0, 1]
        else:
            table_labels = labels

        confusion_matrix_header = []
        for i in range(len(table[0])):
            confusion_matrix_header.append(
                f"{table_labels[i]} (Predicted)"
                if table_labels[i] != "__NOT_CLASSIFIED__"
                else "Not classified"
            )
        for i in range(len(table)):
            table[i].insert(0, f"{table_labels[i]} (Actual)")
        print(
            tabulate(table, headers=confusion_matrix_header, tablefmt="fancy_grid"),
            end="\n\n",
        )


def sort_class_names(class_names):
    if len(class_names) == 2:
        class_names = sorted(list(class_names), reverse=True)
    else:
        class_names = sorted(list(class_names))

    return class_names


class Model:
    def __init__(self, lemmatization=False):
        if lemmatization:
            self.text_vectorizer = SpacyVectorizer
        else:
            self.text_vectorizer = TfidfVectorizer

        self.cross_validation_enabled = True

        self.calculate_importance = True

        self.store_dataset = False

        self.entire_dataset_training = False

        # DBs required for training.
        self.training_dbs: list[str] = []
        # DBs and DB support files required at runtime.
        self.eval_dbs: dict[str, tuple[str, ...]] = {}

        self.le = LabelEncoder()

    def download_eval_dbs(
        self, extract: bool = True, ensure_exist: bool = True
    ) -> None:
        for eval_db, eval_files in self.eval_dbs.items():
            for eval_file in eval_files:
                if db.is_registered(eval_file):
                    assert db.download(eval_file, extract=extract) or not ensure_exist
                else:
                    assert (
                        db.download_support_file(eval_db, eval_file, extract=extract)
                        or not ensure_exist
                    )

    def get_feature_names(self):
        return []

    def get_human_readable_feature_names(self):
        feature_names = self.get_feature_names()

        cleaned_feature_names = []
        for full_feature_name in feature_names:
            type_, feature_name = full_feature_name.split("__", 1)

            if type_ == "desc":
                feature_name = f"Description contains '{feature_name}'"
            elif type_ == "title":
                feature_name = f"Title contains '{feature_name}'"
            elif type_ == "first_comment":
                feature_name = f"First comment contains '{feature_name}'"
            elif type_ == "comments":
                feature_name = f"Comments contain '{feature_name}'"
            elif type_ == "text":
                feature_name = f"Combined text contains '{feature_name}'"
            elif type_ == "files":
                feature_name = f"File '{feature_name}'"
            elif type_ not in ("data", "couple_data"):
                raise ValueError(f"Unexpected feature type for: {full_feature_name}")

            cleaned_feature_names.append(feature_name)

        return cleaned_feature_names

    def get_important_features(self, cutoff, shap_values):
        # returns top features for a shap_value matrix
        def get_top_features(cutoff, shap_values):
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
            top_features = np.column_stack(
                (top_features, top_feature_indices, is_positive)
            )
            # Sort the array (in decreasing order of importance values)
            top_features = top_features[top_features[:, 0].argsort()][::-1]

            return top_features

        important_features = {}
        important_features["classes"] = {}
        important_features["average"] = get_top_features(
            cutoff, np.sum(np.abs(shap_values), axis=0)
        )
        for num, item in enumerate(shap_values):
            # top features for that class
            top_item_features = get_top_features(cutoff, item)

            # shap values of top average features for that class
            abs_sums = np.abs(item).sum(0)
            rel_sums = abs_sums / abs_sums.sum()
            is_pos = ["+" if shap_sum >= 0 else "-" for shap_sum in item.sum(0)]
            top_avg = [
                is_pos[int(index)] + str(rel_sums[int(index)])
                for importance, index, is_positive in important_features["average"]
            ]

            class_name = self.le.inverse_transform([num])[0]

            important_features["classes"][class_name] = (
                top_item_features,
                top_avg,
            )

        return important_features

    def print_feature_importances(self, important_features, class_probabilities=None):
        feature_names = self.get_human_readable_feature_names()
        # extract importance values from the top features for the predicted class
        # when classifying
        if class_probabilities is not None:
            predicted_class_index = class_probabilities.argmax(axis=-1)[0]
            predicted_class = self.le.inverse_transform([predicted_class_index])[0]

            imp_values = important_features["classes"][predicted_class][0]
            shap_val = []
            top_feature_names = []
            for importance, index, is_positive in imp_values:
                if is_positive:
                    shap_val.append("+" + str(importance))
                else:
                    shap_val.append("-" + str(importance))

                feature_value = np.squeeze(
                    to_array(important_features["values"])[:, int(index)]
                )
                top_feature_names.append(
                    f"{feature_names[int(index)]} = {feature_value.round(decimals=5)}"
                )
            shap_val = [[predicted_class] + shap_val]

        # extract importance values from the top features for all the classes
        # when training
        else:
            top_feature_names = [
                feature_names[int(index)]
                for importance, index, is_pos in important_features["average"]
            ]
            shap_val = [
                [class_name] + imp_values[1]
                for class_name, imp_values in important_features["classes"].items()
            ]

        # allow maximum of 3 columns in a row to fit the page better
        COLUMNS = 3
        logger.info("Top {} features:".format(len(top_feature_names)))
        for i in range(0, len(top_feature_names), COLUMNS):
            table = []
            for item in shap_val:
                table.append(item[i : i + COLUMNS])
            print(
                tabulate(
                    table,
                    headers=(["classes"] + top_feature_names)[i : i + COLUMNS],
                    tablefmt="grid",
                ),
                end="\n\n",
            )

    def save_feature_importances(self, important_features, feature_names):
        # Returns a JSON-encodable dictionary that can be saved in the metrics
        # report
        feature_report = {"classes": {}, "average": {}}
        top_feature_names = []

        for importance, index, is_pos in important_features["average"]:
            feature_name = feature_names[int(index)]

            top_feature_names.append(feature_name)
            feature_report["average"][feature_name] = importance

        for i, feature_name in enumerate(top_feature_names):
            for class_name, imp_values in important_features["classes"].items():
                class_report = feature_report["classes"].setdefault(
                    class_name.item(), {}
                )
                class_report[feature_name] = float(imp_values[1][i])

        return feature_report

    def train_test_split(self, X, y):
        return train_test_split(X, y, test_size=0.1, random_state=0)

    def evaluation(self):
        """Subclasses can implement their own additional evaluation."""

    def get_labels(self) -> tuple[dict[Any, Any], list[Any]]:
        """Subclasses implement their own function to gather labels."""
        raise NotImplementedError("The model must implement this method")

    def train(self, importance_cutoff=0.15, limit=None):
        classes, self.class_names = self.get_labels()
        self.class_names = sort_class_names(self.class_names)

        # Get items and labels, filtering out those for which we have no labels.
        X_gen, y = split_tuple_generator(lambda: self.items_gen(classes))

        # Extract features from the items.
        X = self.extraction_pipeline.transform(X_gen)

        # Calculate labels.
        y = np.array(y)
        self.le.fit(y)

        if limit:
            X = X[:limit]
            y = y[:limit]

        logger.info(f"X: {X.shape}, y: {y.shape}")

        is_multilabel = isinstance(y[0], np.ndarray)
        is_binary = len(self.class_names) == 2

        # Split dataset in training and test.
        X_train, X_test, y_train, y_test = self.train_test_split(X, y)

        tracking_metrics = {}

        # Use k-fold cross validation to evaluate results.
        if self.cross_validation_enabled:
            scorings = ["accuracy"]
            if len(self.class_names) == 2:
                scorings += ["precision", "recall"]

            scores = cross_validate(
                self.clf, X_train, self.le.transform(y_train), scoring=scorings, cv=5
            )

            logger.info("Cross Validation scores:")
            for scoring in scorings:
                score = scores[f"test_{scoring}"]
                tracking_metrics[f"test_{scoring}"] = {
                    "mean": score.mean(),
                    "std": score.std() * 2,
                }
                logger.info(
                    f"{scoring.capitalize()}: f{score.mean()} (+/- {score.std() * 2})"
                )

        logger.info(f"X_train: {X_train.shape}, y_train: {y_train.shape}")
        logger.info(f"X_test: {X_test.shape}, y_test: {y_test.shape}")

        self.clf.fit(X_train, self.le.transform(y_train))
        logger.info("Number of features: %d", self.clf.steps[-1][1].n_features_in_)

        logger.info("Model trained")

        feature_names = self.get_human_readable_feature_names()
        if self.calculate_importance and len(feature_names):
            explainer = shap.TreeExplainer(self.clf.named_steps["estimator"])
            _X_train = get_transformer_pipeline(self.clf).transform(X_train)
            shap_values = explainer.shap_values(_X_train)

            # In the binary case, sometimes shap returns a single shap values matrix.
            if is_binary and not isinstance(shap_values, list):
                shap_values = [-shap_values, shap_values]
                summary_plot_value = shap_values[1]
                summary_plot_type = "layered_violin"
            else:
                summary_plot_value = shap_values
                summary_plot_type = None

            shap.summary_plot(
                summary_plot_value,
                to_array(_X_train),
                feature_names=feature_names,
                class_names=self.class_names,
                plot_type=summary_plot_type,
                show=False,
            )

            matplotlib.pyplot.savefig("feature_importance.png", bbox_inches="tight")
            matplotlib.pyplot.xlabel("Impact on model output")
            matplotlib.pyplot.clf()

            important_features = self.get_important_features(
                importance_cutoff, shap_values
            )

            self.print_feature_importances(important_features)

            # Save the important features in the metric report too
            feature_report = self.save_feature_importances(
                important_features, feature_names
            )

            tracking_metrics["feature_report"] = feature_report

        logger.info("Training Set scores:")
        y_pred = self.clf.predict(X_train)
        y_pred = self.le.inverse_transform(y_pred)
        if not is_multilabel:
            print(
                classification_report_imbalanced(
                    y_train, y_pred, labels=self.class_names
                )
            )

        logger.info("Test Set scores:")
        # Evaluate results on the test set.
        y_pred = self.clf.predict(X_test)
        y_pred = self.le.inverse_transform(y_pred)

        if is_multilabel:
            assert isinstance(
                y_pred[0], np.ndarray
            ), "The predictions should be multilabel"

        logger.info(f"No confidence threshold - {len(y_test)} classified")
        if is_multilabel:
            confusion_matrix = metrics.multilabel_confusion_matrix(y_test, y_pred)
        else:
            confusion_matrix = metrics.confusion_matrix(
                y_test, y_pred, labels=self.class_names
            )

            print(
                classification_report_imbalanced(
                    y_test, y_pred, labels=self.class_names
                )
            )
            report = classification_report_imbalanced_values(
                y_test, y_pred, labels=self.class_names
            )

            tracking_metrics["report"] = report

        print_labeled_confusion_matrix(
            confusion_matrix, self.class_names, is_multilabel=is_multilabel
        )

        tracking_metrics["confusion_matrix"] = confusion_matrix.tolist()

        confidence_thresholds = [0.6, 0.7, 0.8, 0.9]

        if is_binary:
            confidence_thresholds = [0.1, 0.2, 0.3, 0.4] + confidence_thresholds

        # Evaluate results on the test set for some confidence thresholds.
        for confidence_threshold in confidence_thresholds:
            y_pred_probas = self.clf.predict_proba(X_test)
            confidence_class_names = self.class_names + ["__NOT_CLASSIFIED__"]

            y_pred_filter = []
            classified_indices = []
            for i in range(0, len(y_test)):
                if not is_binary:
                    argmax = np.argmax(y_pred_probas[i])
                else:
                    argmax = 1 if y_pred_probas[i][1] > confidence_threshold else 0

                if y_pred_probas[i][argmax] < confidence_threshold:
                    if not is_multilabel:
                        y_pred_filter.append("__NOT_CLASSIFIED__")
                    continue

                classified_indices.append(i)
                if is_multilabel:
                    y_pred_filter.append(y_pred[i])
                else:
                    y_pred_filter.append(argmax)

            if not is_multilabel:
                y_pred_filter = np.array(y_pred_filter)
                y_pred_filter[classified_indices] = self.le.inverse_transform(
                    np.array(y_pred_filter[classified_indices], dtype=int)
                )

            if is_multilabel:
                classified_num = len(classified_indices)
            else:
                classified_num = sum(
                    1 for v in y_pred_filter if v != "__NOT_CLASSIFIED__"
                )

            logger.info(
                f"\nConfidence threshold > {confidence_threshold} - {classified_num} classified"
            )
            if is_multilabel:
                confusion_matrix = metrics.multilabel_confusion_matrix(
                    y_test[classified_indices], np.asarray(y_pred_filter)
                )
            else:
                confusion_matrix = metrics.confusion_matrix(
                    y_test.astype(str),
                    y_pred_filter.astype(str),
                    labels=confidence_class_names,
                )
                print(
                    classification_report_imbalanced(
                        y_test.astype(str),
                        y_pred_filter.astype(str),
                        labels=confidence_class_names,
                    )
                )
            print_labeled_confusion_matrix(
                confusion_matrix, confidence_class_names, is_multilabel=is_multilabel
            )

        self.evaluation()

        if self.entire_dataset_training:
            logger.info("Retraining on the entire dataset...")

            X_train = X
            y_train = y

            logger.info(f"X_train: {X_train.shape}, y_train: {y_train.shape}")

            self.clf.fit(X_train, self.le.transform(y_train))

        model_directory = self.__class__.__name__.lower()
        makedirs(model_directory, exist_ok=True)

        step_name, estimator = self.clf.steps.pop()
        if issubclass(type(estimator), XGBModel):
            xgboost_model_path = path.join(model_directory, "xgboost.ubj")
            estimator.save_model(xgboost_model_path)

            # Since we save the estimator separately, we need to reset it to
            # prevent its data from being pickled with the pipeline.
            hyperparameters = estimator.get_params()
            estimator = estimator.__class__(**hyperparameters)
        self.clf.steps.append((step_name, estimator))

        model_path = path.join(model_directory, "model.pkl")
        with open(model_path, "wb") as f:
            pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)

        if self.store_dataset:
            with open(f"{self.__class__.__name__.lower()}_data_X", "wb") as f:
                pickle.dump(X, f, protocol=pickle.HIGHEST_PROTOCOL)

            with open(f"{self.__class__.__name__.lower()}_data_y", "wb") as f:
                pickle.dump(y, f, protocol=pickle.HIGHEST_PROTOCOL)

        return tracking_metrics

    @staticmethod
    def load(model_directory: str) -> "Model":
        model_path = path.join(model_directory, "model.pkl")
        with open(model_path, "rb") as f:
            model = pickle.load(f)

        xgboost_model_path = path.join(model_directory, "xgboost.ubj")
        if path.exists(xgboost_model_path):
            model.clf.named_steps["estimator"].load_model(xgboost_model_path)

        return model

    def overwrite_classes(self, items, classes, probabilities):
        return classes

    def classify(
        self,
        items,
        probabilities=False,
        importances=False,
        importance_cutoff=0.15,
        background_dataset=None,
    ):
        assert items is not None
        assert (
            self.extraction_pipeline is not None and self.clf is not None
        ), "The module needs to be initialized first"

        if not isinstance(items, list):
            items = [items]

        assert isinstance(items[0], (dict, tuple))

        X = self.extraction_pipeline.transform(lambda: items)
        if probabilities:
            classes = self.clf.predict_proba(X)
        else:
            classes = self.clf.predict(X)

        classes = self.overwrite_classes(items, classes, probabilities)

        if importances:
            pred_class_index = classes.argmax(axis=-1)[0]
            pred_class = self.le.inverse_transform([pred_class_index])[0]

            if background_dataset is None:
                explainer = shap.TreeExplainer(self.clf.named_steps["estimator"])
            else:
                explainer = shap.TreeExplainer(
                    self.clf.named_steps["estimator"],
                    to_array(background_dataset(pred_class)),
                    feature_perturbation="interventional",
                )

            _X = get_transformer_pipeline(self.clf).transform(X)
            shap_values = explainer.shap_values(to_array(_X))

            # In the binary case, sometimes shap returns a single shap values matrix.
            if len(classes[0]) == 2 and not isinstance(shap_values, list):
                shap_values = [-shap_values, shap_values]

            important_features = self.get_important_features(
                importance_cutoff, shap_values
            )
            important_features["values"] = _X

            top_indexes = [
                int(index)
                for _, index, _ in important_features["classes"][pred_class][0]
            ]

            feature_names = self.get_human_readable_feature_names()

            feature_legend = {
                str(i + 1): feature_names[feature_i]
                for i, feature_i in enumerate(top_indexes)
            }

            return (
                classes,
                {"importances": important_features, "feature_legend": feature_legend},
            )

        return classes

    def check(self):
        """Ensure everything is OK.

        Subclasses can implement their own check, the base model doesn't check
        anything at the moment.
        """
        return True

    def get_extra_data(self):
        """Get extra data for the model.

        Returns:
            a dict that can be used for customers who need static extra data for
            a given model. Must return a dict with JSON-encodable types.
        """
        return {}


class BugModel(Model):
    def __init__(self, lemmatization=False, commit_data=False):
        Model.__init__(self, lemmatization)
        self.commit_data = commit_data
        self.training_dbs = [bugzilla.BUGS_DB]
        if commit_data:
            self.training_dbs.append(repository.COMMITS_DB)

    def items_gen(self, classes):
        if not self.commit_data:
            commit_map = None
        else:
            commit_map = defaultdict(list)

            for commit in repository.get_commits():
                bug_id = commit["bug_id"]
                if not bug_id:
                    continue

                commit_map[bug_id].append(commit)

            assert len(commit_map) > 0

        for bug in bugzilla.get_bugs():
            bug_id = bug["id"]
            if bug_id not in classes:
                continue

            if self.commit_data:
                if bug_id in commit_map:
                    bug["commits"] = commit_map[bug_id]
                else:
                    bug["commits"] = []

            yield bug, classes[bug_id]


class CommitModel(Model):
    def __init__(self, lemmatization=False, bug_data=False):
        Model.__init__(self, lemmatization)
        self.bug_data = bug_data
        self.training_dbs = [repository.COMMITS_DB]
        if bug_data:
            self.training_dbs.append(bugzilla.BUGS_DB)

    def items_gen(self, classes):
        if not self.bug_data:
            bug_map = None
        else:
            all_bug_ids = set(
                commit["bug_id"]
                for commit in repository.get_commits()
                if commit["node"] in classes
            )

            bug_map = {
                bug["id"]: bug
                for bug in bugzilla.get_bugs()
                if bug["id"] in all_bug_ids
            }

            assert len(bug_map) > 0

        for commit in repository.get_commits(include_ignored=True):
            if commit["node"] not in classes:
                continue

            if self.bug_data:
                if commit["bug_id"] in bug_map:
                    commit["bug"] = bug_map[commit["bug_id"]]
                else:
                    commit["bug"] = {}

            yield commit, classes[commit["node"]]


class IssueModel(Model):
    def __init__(self, owner, repo, lemmatization=False):
        Model.__init__(self, lemmatization)

        self.github = Github(owner=owner, repo=repo)
        self.training_dbs = [self.github.db_path]

    def items_gen(self, classes):
        for issue in self.github.get_issues():
            issue_number = issue["number"]
            if issue_number not in classes:
                continue

            yield issue, classes[issue_number]
