# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest

from bugbug.commit_features import CommitExtractor, author_experience, reviewers_num
from bugbug.feature_cleanup import fileref, url

COMMIT_EXTRACTOR_PARAMS = [
    ([author_experience, author_experience], [fileref, url]),
    ([reviewers_num, author_experience], [fileref, fileref]),
]


@pytest.mark.parametrize(
    "feature_extractors,cleanup_functions", COMMIT_EXTRACTOR_PARAMS
)
def test_CommitExtractor(feature_extractors, cleanup_functions):
    CommitExtractor([reviewers_num, author_experience], [fileref, url])
    with pytest.raises(AssertionError):
        CommitExtractor(feature_extractors, cleanup_functions)
