# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Which commits the perf regression predictor never looks at.

The prediction endpoint and the training set export share these rules, so a
kind of commit the model never scores is not trained on either. Backouts are
not among them: a revert is a real code change and regresses benchmarks like
any other, so they are scored and trained on.
"""

from typing import Callable

from bugbug import repository

# Keyed by the reason reported to the client. Rules are evaluated in order and
# the first match wins. Each predicate receives a changeset with the
# hg.mozilla.org automationrelevance field names.
IGNORED_COMMIT_RULES: dict[str, Callable[[dict], bool]] = {
    # Merges (e.g. mozilla-central into autoland) carry no change of their own.
    "merge": lambda changeset: len(changeset.get("parents", [])) > 1,
    # Automated web-platform-tests imports from upstream.
    "wptsync": lambda changeset: repository.is_wptsync(
        {
            "author_email": changeset.get("author", ""),
            "desc": changeset.get("desc", ""),
        }
    ),
    # Large mechanical changes (e.g. reformatting) excluded from blame.
    "ignore-this-changeset": lambda changeset: (
        "ignore-this-changeset" in changeset.get("desc", "")
    ),
}


def get_ignore_reason(changeset: dict) -> str | None:
    """Return why the predictor skips a commit, or None to score it."""
    for reason, matches in IGNORED_COMMIT_RULES.items():
        if matches(changeset):
            return reason
    return None


def changeset_from_export(export: repository.HgExport) -> dict:
    """Build the changeset fields the rules read from a parsed ``hg export``."""
    return {
        "author": export.author,
        "parents": export.parents,
        "desc": export.message,
    }
