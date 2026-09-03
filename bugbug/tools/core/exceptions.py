# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Custom exceptions for bugbug tools."""


class ModelResultError(Exception):
    """Occurs when the model returns an unexpected result."""


class FileNotInPatchError(ModelResultError):
    """Occurs when the file in the model result is not part of the patch."""


class HunkNotInPatchError(ModelResultError):
    """Occurs when the hunk in the model result is not part of the patch."""


class RunawayGenerationError(ModelResultError):
    """Occurs when the model produces a degenerate output.

    This typically happens when the model gets stuck in a next-token loop and
    repeats the same word or phrase many times, or otherwise emits an
    excessively long result (see https://github.com/mozilla/bugbug/issues/5995).
    """


class RecursionLimitError(ModelResultError):
    """Occurs when the agent exceeds the maximum number of recursive steps."""


class LargeDiffError(Exception):
    """Occurs when the diff is too large to be processed."""
