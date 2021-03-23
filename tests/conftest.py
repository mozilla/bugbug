# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import shutil

import pytest
import zstandard

from bugbug import bugzilla, github, repository

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(autouse=True)
def mock_data(tmp_path):
    os.mkdir(tmp_path / "data")

    DBs = [
        os.path.basename(bugzilla.BUGS_DB),
        os.path.basename(repository.COMMITS_DB),
        os.path.basename(github.GITHUB_ISSUES_DB),
    ]

    for f in DBs:
        shutil.copyfile(os.path.join(FIXTURES_DIR, f), tmp_path / "data" / f)
        with open(tmp_path / "data" / f"{f}.zst.etag", "w") as f:
            f.write("etag")

    os.chdir(tmp_path)


@pytest.fixture
def get_fixture_path():
    def _get_fixture_path(path):
        path = os.path.join(FIXTURES_DIR, path)
        assert os.path.exists(path)
        return path

    return _get_fixture_path


@pytest.fixture
def mock_zst():
    def create_zst_file(db_path, content=b'{"Hello": "World"}'):
        with open(db_path, "wb") as output_f:
            cctx = zstandard.ZstdCompressor()
            with cctx.stream_writer(output_f) as compressor:
                compressor.write(content)

    return create_zst_file
