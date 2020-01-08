# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest

from bugbug.bug_features import has_str, has_url
from bugbug.commit_features import CommitExtractor
from bugbug.feature_cleanup import fileref, url

COMMIT_EXTRACTOR_PARAMS = [
    ([has_str, has_url], [fileref, url]),
    ([has_str, has_str], [fileref, url]),
    ([has_str, has_url], [fileref, fileref]),
]


@pytest.mark.parametrize(
    "feature_extractors,cleanup_functions", COMMIT_EXTRACTOR_PARAMS
)
def test_CommitExtractor(feature_extractors, cleanup_functions):
    with pytest.raises(AssertionError):
        CommitExtractor(feature_extractors, cleanup_functions)
