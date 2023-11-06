# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest

from bugbug.commit_features import AuthorExperience, CommitExtractor, ReviewersNum
from bugbug.feature_cleanup import fileref, url


def test_CommitExtractor():
    CommitExtractor([ReviewersNum(), AuthorExperience()], [fileref(), url()])
    with pytest.raises(AssertionError):
        CommitExtractor([ReviewersNum(), AuthorExperience()], [fileref(), fileref()])
    with pytest.raises(AssertionError):
        CommitExtractor([AuthorExperience(), AuthorExperience()], [fileref(), url()])
