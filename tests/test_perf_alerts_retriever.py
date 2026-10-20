# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest

from bugbug import db, perf
from scripts import perf_alerts_retriever


@pytest.mark.parametrize("refresh_repo", [False, True])
def test_export_refreshes_the_clone_only_when_asked(monkeypatch, refresh_repo) -> None:
    db.write(perf.PERF_REGRESSIONS_DB, [])
    db.write(perf.PERF_PUSH_STATUS_DB, [])
    calls = []
    monkeypatch.setattr(
        perf_alerts_retriever,
        "clone_repository",
        lambda repo_dir: calls.append(("clone", repo_dir)),
    )

    def attach_commit_patches(records, repo_dir):
        calls.append(("read", repo_dir))
        return []

    monkeypatch.setattr(perf, "attach_commit_patches", attach_commit_patches)

    perf_alerts_retriever.Retriever().export(
        "/cache/mozilla-central",
        settle_days=21,
        negative_ratio=5,
        seed=0,
        test_fraction=0.1,
        refresh_repo=refresh_repo,
    )

    # Refreshing reverts local changes, so it only happens when asked, and
    # always before the clone is read.
    read = ("read", "/cache/mozilla-central")
    clone = ("clone", "/cache/mozilla-central")
    assert calls == ([clone, read] if refresh_repo else [read])
