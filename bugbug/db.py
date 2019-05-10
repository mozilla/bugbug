# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import gzip
import io
import json
import lzma
import os
import pickle
import shutil
from contextlib import contextmanager
from urllib.request import urlretrieve

import zstandard

DATABASES = {}


def register(path, url):
    DATABASES[path] = {"url": url}

    # Create DB parent directory.
    parent_dir = os.path.dirname(path)
    if not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)


# Download and extract databases.
def download():
    for path, info in DATABASES.items():
        if os.path.exists(path):
            continue

        xz_path = f"{path}.xz"

        # Only download if the xz file is not there yet.
        if not os.path.exists(xz_path):
            urlretrieve(DATABASES[path]["url"], xz_path)

        with open(path, "wb") as output_f:
            with lzma.open(xz_path) as input_f:
                shutil.copyfileobj(input_f, output_f)


class Store:
    def __init__(self, fh):
        self.fh = fh


class JSONStore(Store):
    def write(self, elems):
        for elem in elems:
            self.fh.write((json.dumps(elem) + "\n").encode("utf-8"))

    def read(self):
        for line in io.TextIOWrapper(self.fh, encoding="utf-8"):
            yield json.loads(line)


class PickleStore(Store):
    def write(self, elems):
        for elem in elems:
            self.fh.write(pickle.dumps(elem))

    def read(self):
        try:
            while True:
                yield pickle.load(self.fh)
        except EOFError:
            pass


COMPRESSION_FORMATS = ["gz", "zstd"]
SERIALIZATION_FORMATS = {"json": JSONStore, "pickle": PickleStore}


@contextmanager
def _db_open(path, mode):
    parts = str(path).split(".")
    assert len(parts) > 1, "Extension needed to figure out serialization format"
    if len(parts) == 2:
        db_format = parts[-1]
        compression = None
    else:
        db_format = parts[-2]
        compression = parts[-1]

    assert compression is None or compression in COMPRESSION_FORMATS
    assert db_format in SERIALIZATION_FORMATS

    store_constructor = SERIALIZATION_FORMATS[db_format]

    if compression == "gz":
        with gzip.GzipFile(path, mode) as f:
            yield store_constructor(f)
    elif compression == "zstd":
        if "w" in mode or "a" in mode:
            cctx = zstandard.ZstdCompressor()
            with open(path, mode) as f:
                with cctx.stream_writer(f) as writer:
                    yield store_constructor(writer)
        else:
            dctx = zstandard.ZstdDecompressor()
            with open(path, mode) as f:
                with dctx.stream_reader(f) as reader:
                    yield store_constructor(reader)
    else:
        with open(path, mode) as f:
            yield store_constructor(f)


def read(path):
    assert path in DATABASES

    if not os.path.exists(path):
        return ()

    with _db_open(path, "rb") as store:
        for elem in store.read():
            yield elem


def write(path, elems):
    assert path in DATABASES

    with _db_open(path, "wb") as store:
        store.write(elems)


def append(path, elems):
    assert path in DATABASES

    with _db_open(path, "ab") as store:
        store.write(elems)


def delete(path, match):
    assert path in DATABASES

    dirname, basename = os.path.split(path)
    new_path = os.path.join(dirname, f"new_{basename}")

    def matching_elems(store):
        for elem in store.read():
            if not match(elem):
                yield elem

    with _db_open(new_path, "wb") as wstore:
        with _db_open(path, "rb") as rstore:
            wstore.write(matching_elems(rstore))

    os.unlink(path)
    os.rename(new_path, path)
