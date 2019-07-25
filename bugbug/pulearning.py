# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

# Module for Positive Unlabeled Learning
import numpy as np
from sklearn.ensemble import bagging
from sklearn.utils import indices_to_mask


def pu_parallel_build_estimators(
    n_estimators, ensemble, X, y, sample_weight, seeds, total_n_estimators, verbose
):
    """Private function used to build a batch of estimators within a job."""
    # Retrieve settings
    n_samples, n_features = X.shape
    max_features = ensemble._max_features
    max_samples = ensemble._max_samples
    bootstrap = ensemble.bootstrap
    bootstrap_features = ensemble.bootstrap_features
    support_sample_weight = bagging.has_fit_parameter(
        ensemble.base_estimator_, "sample_weight"
    )
    if not support_sample_weight and sample_weight is not None:
        raise ValueError("The base estimator doesn't support sample weight")

    # Build estimators
    estimators = []
    estimators_features = []

    for i in range(n_estimators):
        if verbose > 1:
            print(
                "Building estimator %d of %d for this parallel run "
                "(total %d)..." % (i + 1, n_estimators, total_n_estimators)
            )

        random_state = np.random.RandomState(seeds[i])
        estimator = ensemble._make_estimator(append=False, random_state=random_state)

        Positive_indices = [pair[0] for pair in enumerate(y) if pair[1] == 1]
        Unlabeled_indices = [pair[0] for pair in enumerate(y) if pair[1] < 1]
        features, indices = bagging._generate_bagging_indices(
            random_state,
            bootstrap_features,
            bootstrap,
            n_features,
            len(Unlabeled_indices),
            max_features,
            max_samples,
        )
        indices = [Unlabeled_indices[i] for i in indices] + Positive_indices

        # Draw samples, using sample weights, and then fit
        if support_sample_weight:
            if sample_weight is None:
                curr_sample_weight = np.ones((n_samples,))
            else:
                curr_sample_weight = sample_weight.copy()

            if bootstrap:
                sample_counts = np.bincount(indices, minlength=n_samples)
                curr_sample_weight *= sample_counts
            else:
                not_indices_mask = ~indices_to_mask(indices, n_samples)
                curr_sample_weight[not_indices_mask] = 0

            estimator.fit(X[:, features], y, sample_weight=curr_sample_weight)

        # Draw samples, using a mask, and then fit
        else:
            estimator.fit((X[indices])[:, features], y[indices])

        estimators.append(estimator)
        estimators_features.append(features)

    return estimators, estimators_features


bagging._parallel_build_estimators = pu_parallel_build_estimators


class PUClassifier(bagging.BaggingClassifier):
    def __init__(
        self,
        base_estimator=None,
        n_estimators=10,
        max_samples=1.0,
        max_features=1.0,
        bootstrap=True,
        bootstrap_features=False,
        oob_score=True,
        warm_start=False,
        n_jobs=None,
        random_state=None,
        verbose=0,
    ):
        super().__init__(
            base_estimator,
            n_estimators=n_estimators,
            max_samples=max_samples,
            max_features=max_features,
            bootstrap=bootstrap,
            bootstrap_features=bootstrap_features,
            oob_score=oob_score,
            warm_start=warm_start,
            n_jobs=n_jobs,
            random_state=random_state,
            verbose=verbose,
        )

    def fit(self, X, y, sample_weight=None):
        self.y = y
        return self._fit(X, y, self.max_samples, sample_weight=sample_weight)

    def _get_estimators_indices(self):
        # Get drawn indices along both sample and feature axes
        for seed in self._seeds:
            # Operations accessing random_state must be performed identically
            # to those in `_parallel_build_estimators()`
            random_state = np.random.RandomState(seed)

            Positive_indices = [pair[0] for pair in enumerate(self.y) if pair[1] == 1]
            Unlabeled_indices = [pair[0] for pair in enumerate(self.y) if pair[1] < 1]

            feature_indices, sample_indices = bagging._generate_bagging_indices(
                random_state,
                self.bootstrap_features,
                self.bootstrap,
                self.n_features_,
                len(Unlabeled_indices),
                self._max_features,
                self._max_samples,
            )

            sample_indices = [
                Unlabeled_indices[i] for i in sample_indices
            ] + Positive_indices

            yield feature_indices, sample_indices
