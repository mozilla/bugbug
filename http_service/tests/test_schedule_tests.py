# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug_http.models import schedule_tests


def test_schedule(patch_resources, mock_hgmo, mock_repo):

    # The repo should be almost empty at first
    repo_dir, repo = mock_repo
    assert len(repo.log()) == 1
    test_txt = repo_dir / "test.txt"
    assert test_txt.exists()
    assert test_txt.read_text("utf-8") == "Line 1"

    # Scheduling a test on a revision should apply changes in the repo
    schedule_tests("mozilla-central", "12345deadbeef")

    # Check changes have been applied
    assert len(repo.log()) == 2
    assert test_txt.read_text("utf-8") == "Line 1\nLine 2\n"
