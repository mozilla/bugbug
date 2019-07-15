# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import collections
import json
import os
import time

import dateutil.parser
import numpy as np
import requests
import taskcluster
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OrdinalEncoder

TASKCLUSTER_DEFAULT_URL = "https://taskcluster.net"


def split_tuple_iterator(iterable):
    q = collections.deque()

    def first_iter():
        for first, second in iterable:
            yield first
            q.append(second)

    return first_iter(), q


def numpy_to_dict(array):
    return {name: array[name].squeeze(axis=1) for name in array.dtype.names}


class StructuredColumnTransformer(ColumnTransformer):
    def _hstack(self, Xs):
        result = super()._hstack(Xs)

        transformer_names = (name for name, transformer, column in self.transformers_)
        types = []
        for i, (f, transformer_name) in enumerate(zip(Xs, transformer_names)):
            types.append((transformer_name, result.dtype, (f.shape[1],)))

        return result.todense().view(np.dtype(types))


class DictExtractor(BaseEstimator, TransformerMixin):
    def __init__(self, key):
        self.key = key

    def fit(self, x, y=None):
        return self

    def transform(self, data):
        return np.array([elem[self.key] for elem in data]).reshape(-1, 1)


class MissingOrdinalEncoder(OrdinalEncoder):
    """
    Ordinal encoder that ignores missing values encountered after training.
    Workaround for issue: scikit-learn/scikit-learn#11997
    """

    def fit(self, X, y=None):
        self._categories = self.categories
        self._fit(X, handle_unknown="ignore")
        return self

    def transform(self, X):
        X_int, _ = self._transform(X, handle_unknown="ignore")
        return X_int.astype(self.dtype, copy=False)


def get_taskcluster_options():
    """
    Helper to get the Taskcluster setup options
    according to current environment (local or Taskcluster)
    """
    options = taskcluster.optionsFromEnvironment()
    proxy_url = os.environ.get("TASKCLUSTER_PROXY_URL")

    if proxy_url is not None:
        # Always use proxy url when available
        options["rootUrl"] = proxy_url

    if "rootUrl" not in options:
        # Always have a value in root url
        options["rootUrl"] = TASKCLUSTER_DEFAULT_URL

    return options


def get_secret(secret_id):
    """ Return the secret value
    """
    env_variable_name = f"BUGBUG_{secret_id}"

    # Try in the environment first
    secret_from_env = os.environ.get(env_variable_name)

    if secret_from_env:
        return secret_from_env

    # If not in env, try with TC if we have the secret id
    tc_secret_id = os.environ.get("TC_SECRET_ID")

    if tc_secret_id:
        secrets = taskcluster.Secrets(get_taskcluster_options())
        secret_bucket = secrets.get(tc_secret_id)

        return secret_bucket["secret"][secret_id]

    else:
        raise ValueError("Failed to find secret {}".format(secret_id))


def download_check_etag(url, path):
    r = requests.head(url, allow_redirects=True)
    new_etag = r.headers["ETag"]

    try:
        with open(f"{path}.etag", "r") as f:
            old_etag = f.read()
    except IOError:
        old_etag = None

    if old_etag != new_etag:
        r = requests.get(url, stream=True)
        r.raise_for_status()

        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=4096):
                f.write(chunk)

        with open(f"{path}.etag", "w") as f:
            f.write(new_etag)


def get_last_modified(url):
    r = requests.head(url, allow_redirects=True)
    if "Last-Modified" not in r.headers:
        return None

    return dateutil.parser.parse(r.headers["Last-Modified"])


def retry(operation, retries=5, wait_between_retries=30):
    while True:
        try:
            return operation()
        except Exception:
            retries -= 1
            if retries == 0:
                raise

            time.sleep(wait_between_retries)


class CustomJsonEncoder(json.JSONEncoder):
    """ A custom Json Encoder to support Numpy types
    """

    def default(self, obj):
        try:
            return np.asscalar(obj)
        except (ValueError, IndexError, AttributeError, TypeError):
            pass

        return super().default(self, obj)
