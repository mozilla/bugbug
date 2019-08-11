# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

# Author: Gilles Louppe <g.louppe@gmail.com>
# License: BSD 3 clause
#
#
# Adapted for PU learning by Roy Wright <roy.w.wright@gmail.com>
# (work in progress)
#
# A better idea: instead of a separate PU class, modify the original
# sklearn BaggingClassifier so that the parameters `max_samples`
# and `bootstrap` may be lists or dicts...
# e.g. for a PU problem with 500 positives and 10000 unlabeled, we might set
# max_samples = [500, 500]     (to balance P and U in each bag)
# bootstrap = [True, False]    (to only bootstrap the unlabeled)

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
    max_samples = sum(y)
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

        ###################modified part for PULearning########################
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
        #######################################################################

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
        oob_score=False,
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
