# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import concurrent.futures
import errno
import json
import logging
import os
import re
import socket
import subprocess
import tarfile
import urllib.parse
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from typing import Any, Iterator, Optional

import boto3
import dateutil.parser
import lmdb
import numpy as np
import psutil
import requests
import scipy
import taskcluster
import zstandard
from pkg_resources import DistributionNotFound
from requests.packages.urllib3.util.retry import Retry
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OrdinalEncoder

from bugbug import get_bugbug_version

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TASKCLUSTER_DEFAULT_URL = "https://community-tc.services.mozilla.com"


def split_tuple_generator(generator):
    second_filled = False
    q = deque()

    def first_iter():
        nonlocal second_filled

        for first, second in generator():
            yield first
            if not second_filled:
                q.append(second)

        second_filled = True

    return first_iter, q


def numpy_to_dict(array):
    return {name: array[name].squeeze(axis=1) for name in array.dtype.names}


def to_array(val):
    if isinstance(val, scipy.sparse.csr_matrix):
        return val.toarray()

    return val


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


def get_taskcluster_options() -> dict:
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


def get_secret(secret_id: str) -> Any:
    """Return the secret value"""
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


def get_s3_credentials() -> dict:
    auth = taskcluster.Auth(get_taskcluster_options())
    response = auth.awsS3Credentials("read-write", "communitytc-bugbug", "data/")
    return response["credentials"]


def upload_s3(paths: str) -> None:
    credentials = get_s3_credentials()

    client = boto3.client(
        "s3",
        aws_access_key_id=credentials["accessKeyId"],
        aws_secret_access_key=credentials["secretAccessKey"],
        aws_session_token=credentials["sessionToken"],
    )
    transfer = boto3.s3.transfer.S3Transfer(client)

    for path in paths:
        assert path.startswith("data/")
        transfer.upload_file(path, "communitytc-bugbug", path)


def download_check_etag(url, path=None):
    r = requests.head(url, allow_redirects=True)

    if path is None:
        path = url.split("/")[-1]

    new_etag = r.headers["ETag"]

    try:
        with open(f"{path}.etag", "r") as f:
            old_etag = f.read()
    except IOError:
        old_etag = None

    if old_etag == new_etag:
        return False

    r = requests.get(url, stream=True)
    r.raise_for_status()

    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1048576):
            f.write(chunk)

    r.raise_for_status()

    with open(f"{path}.etag", "w") as f:
        f.write(new_etag)

    return True


def get_last_modified(url: str) -> Optional[datetime]:
    r = requests.head(url, allow_redirects=True)

    if "Last-Modified" not in r.headers:
        return None

    return dateutil.parser.parse(r.headers["Last-Modified"])


def download_model(model_name: str) -> str:
    version = os.getenv("TAG")
    if not version:
        try:
            version = f"v{get_bugbug_version()}"
        except DistributionNotFound:
            version = "latest"

    path = f"{model_name}model"
    url = f"https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.train_{model_name}.{version}/artifacts/public/{path}.zst"
    logger.info(f"Downloading {url}...")
    updated = download_check_etag(url)
    if updated:
        zstd_decompress(path)
        os.remove(f"{path}.zst")
    assert os.path.exists(path), "Decompressed file exists"
    return path


def zstd_compress(path: str) -> None:
    if not os.path.exists(path):
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)

    subprocess.run(["zstd", "-f", path], check=True)


def zstd_decompress(path: str) -> None:
    dctx = zstandard.ZstdDecompressor()
    with open(f"{path}.zst", "rb") as input_f:
        with open(path, "wb") as output_f:
            dctx.copy_stream(input_f, output_f)


@contextmanager
def open_tar_zst(path: str, mode: str) -> Iterator[tarfile.TarFile]:
    if mode == "w":
        cctx = zstandard.ZstdCompressor(threads=-1)
        with open(path, "wb") as f:
            with cctx.stream_writer(f) as compressor:
                with tarfile.open(mode="w|", fileobj=compressor) as tar:
                    yield tar
    elif mode == "r":
        dctx = zstandard.ZstdDecompressor()
        with open(path, "rb") as f:
            with dctx.stream_reader(f) as reader:
                with tarfile.open(mode="r|", fileobj=reader) as tar:
                    yield tar
    else:
        assert False, f"Unexpected mode: {mode}"


# Using tar directly is twice as fast than through Python!
def create_tar_zst(path: str) -> None:
    inner_path = path[: -len(".tar.zst")]

    if not os.path.exists(inner_path):
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), inner_path)

    subprocess.run(["tar", "-I", "zstd", "-cf", path, inner_path], check=True)


def extract_tar_zst(path: str) -> None:
    if not os.path.exists(path):
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)

    subprocess.run(["tar", "-I", "zstd", "-xf", path], check=True)


def extract_file(path: str) -> None:
    inner_path, _ = os.path.splitext(path)

    if str(path).endswith(".tar.zst"):
        extract_tar_zst(path)
    elif str(path).endswith(".zst"):
        zstd_decompress(inner_path)
    else:
        assert False, f"Unexpected compression type for {path}"


class CustomJsonEncoder(json.JSONEncoder):
    """A custom Json Encoder to support Numpy types"""

    def default(self, obj):
        try:
            return np.asscalar(obj)
        except (ValueError, IndexError, AttributeError, TypeError):
            pass

        return super().default(obj)


class ExpQueue:
    def __init__(self, start_day: int, maxlen: int, default: Any) -> None:
        self.list = deque([default] * maxlen, maxlen=maxlen)
        self.start_day = start_day - (maxlen - 1)
        self.default = default

    def __deepcopy__(self, memo):
        result = ExpQueue.__new__(ExpQueue)

        # We don't need to deepcopy the list, as elements in the list are immutable.
        result.list = self.list.copy()
        result.start_day = self.start_day
        result.default = self.default

        return result

    @property
    def last_day(self) -> int:
        assert self.list.maxlen is not None
        return self.start_day + (self.list.maxlen - 1)

    def __getitem__(self, day: int) -> Any:
        assert (
            day >= self.start_day
        ), f"Can't get a day ({day}) from earlier than start day ({self.start_day})"

        if day < 0:
            return self.default

        if day > self.last_day:
            return self.list[-1]

        return self.list[day - self.start_day]

    def __setitem__(self, day: int, value: Any) -> None:
        assert self.list.maxlen is not None
        if day == self.last_day:
            self.list[day - self.start_day] = value
        elif day > self.last_day:
            last_val = self.list[-1]
            # We need to extend the list except for 2 elements (the last, which
            # is going to be the same, and the one we are adding now).
            range_end = min(day - self.last_day, self.list.maxlen) - 2
            if range_end > 0:
                self.list.extend(last_val for _ in range(range_end))

            self.start_day = day - (self.list.maxlen - 1)

            self.list.append(value)
        else:
            assert False, "Can't insert in the past"

        assert day == self.last_day


class LMDBDict:
    def __init__(self, path: str, readonly: bool = False):
        self.readonly = readonly
        self.db = lmdb.open(
            path,
            map_size=68719476736,
            metasync=False,
            sync=False,
            meminit=False,
            readonly=readonly,
        )
        self.txn = self.db.begin(buffers=True, write=not readonly)

    def close(self) -> None:
        self.txn.commit()
        if not self.readonly:
            self.db.sync()
        self.db.close()

    def __contains__(self, key: bytes) -> bool:
        return self.txn.get(key) is not None

    def __getitem__(self, key: bytes) -> Any:
        val = self.txn.get(key)
        if val is None:
            raise KeyError
        return val

    def __setitem__(self, key: bytes, value: Any) -> None:
        self.txn.put(key, value, dupdata=False)


def get_free_tcp_port() -> int:
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp.bind(("", 0))
    addr, port = tcp.getsockname()
    tcp.close()
    return port


class ThreadPoolExecutorResult(concurrent.futures.ThreadPoolExecutor):
    def __init__(self, *args, **kwargs):
        self.futures = []
        super(ThreadPoolExecutorResult, self).__init__(*args, **kwargs)

    def submit(self, *args, **kwargs):
        future = super(ThreadPoolExecutorResult, self).submit(*args, **kwargs)
        self.futures.append(future)
        return future

    def __exit__(self, *args):
        try:
            for future in concurrent.futures.as_completed(self.futures):
                future.result()
        except Exception as e:
            for future in self.futures:
                future.cancel()
            raise e
        return super(ThreadPoolExecutorResult, self).__exit__(*args)


@lru_cache(maxsize=None)
def get_session(name: str) -> requests.Session:
    session = requests.Session()

    retry = Retry(total=9, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])

    # Default HTTPAdapter uses 10 connections. Mount custom adapter to increase
    # that limit. Connections are established as needed, so using a large value
    # should not negatively impact performance.
    http_adapter = requests.adapters.HTTPAdapter(
        pool_connections=50, pool_maxsize=50, max_retries=retry
    )
    session.mount("https://", http_adapter)
    session.mount("http://", http_adapter)

    return session


def get_hgmo_stack(branch: str, revision: str) -> list[bytes]:
    """Load descriptions of patches in the stack for a given revision"""
    url = f"https://hg.mozilla.org/{branch}/json-automationrelevance/{revision}"
    r = get_session("hgmo").get(url)
    r.raise_for_status()

    def should_skip(changeset):
        # Analyze all changesets if we are not on try.
        if branch != "try":
            return False

        # Don't analyze public changesets, as they are not relevant for a try
        # push.
        if changeset["phase"] != "draft":
            return True

        # Don't analyze empty changesets.
        if len(changeset["files"]) == 0:
            return True

        # Don't analyze changesets which simply specify try parameters.
        if changeset["files"] == ["try_task_config.json"]:
            return True

        return False

    return [
        c["node"].encode("ascii") for c in r.json()["changesets"] if not should_skip(c)
    ]


def get_physical_cpu_count() -> int:
    return psutil.cpu_count(logical=False)


def extract_metadata(body: str) -> dict:
    """Extract metadata as dict from github issue body.

    Extract all metadata items and return a dictionary
    Example metadata format: <!-- @public_url: *** -->
    """
    match_list = re.findall(r"<!--\s@(\w+):\s(.+)\s-->", body)
    return dict(match_list)


def extract_private(issue_body: str) -> Optional[tuple]:
    """Extract private issue information from public issue body

    Parse public issue body and extract private issue number and
    its owner/repository (webcompat repository usecase)
    """
    private_url = extract_metadata(issue_body).get("private_url", "").strip()
    private_issue_path = urllib.parse.urlparse(private_url).path

    if private_issue_path:
        owner, repo, _, number = tuple(private_issue_path.split("/")[1:])
        return owner, repo, number

    return None


def escape_markdown(text: str) -> str:
    return (
        text.replace("*", "\\*")
        .replace("`", "\\`")
        .replace("_", "\\_")
        .replace("~", "\\~")
        .replace(">", "\\>")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("|", "\\|")
    )
