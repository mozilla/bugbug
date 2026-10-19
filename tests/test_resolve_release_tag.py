# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest

from infra.resolve_release_tag import (
    DEPLOY_METADATA_PATH,
    get_prod_version,
    resolve_tags,
)


@pytest.mark.parametrize("version", ["v0.0.386", "0.0.386"])
def test_get_prod_version(tmp_path, version):
    metadata_path = tmp_path / "deploy_metadata.toml"
    metadata_path.write_text(f'current_prod_version = "{version}"\n')

    assert get_prod_version(str(metadata_path)) == "0.0.386"


def test_get_prod_version_from_repo_metadata():
    assert not get_prod_version(DEPLOY_METADATA_PATH).startswith("v")


def test_promote_prod_version():
    assert resolve_tags(None, "0.0.386") == ("v0.0.386", ["v0.0.386-prod"])


@pytest.mark.parametrize("image_tag", ["v0.0.387", "0.0.387"])
def test_new_version_goes_to_stage_only(image_tag):
    assert resolve_tags(image_tag, "0.0.386") == ("v0.0.387", ["v0.0.387"])


@pytest.mark.parametrize("image_tag", ["v0.0.386", "0.0.386"])
def test_retrained_prod_version_goes_to_stage_and_prod(image_tag):
    assert resolve_tags(image_tag, "0.0.386") == (
        "v0.0.386",
        ["v0.0.386", "v0.0.386-prod"],
    )
