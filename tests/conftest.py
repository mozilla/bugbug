# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import shutil

import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(scope="session")
def mock_data(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("")
    print(tmp_path)
    os.mkdir(tmp_path / "data")

    shutil.copyfile(
        os.path.join(FIXTURES_DIR, "bugs.json"), tmp_path / "data" / "bugs.json"
    )

    os.chdir(tmp_path)


@pytest.fixture
def get_fixture_path():
    def _get_fixture_path(path):
        path = os.path.join(FIXTURES_DIR, path)
        assert os.path.exists(path)
        return path

    return _get_fixture_path
