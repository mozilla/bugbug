# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Code review agent and related modules.

This package provides:
- Agent: CodeReviewTool for automated code review
- Database: Vector database for review comments and feedback
- Prompts: Templates for code review
- LangChain Tools: Tools for the review agent

For backward compatibility, also exports platform classes and utilities.
"""

# Agent
from bugbug.tools.code_review.agent import CodeReviewTool

# Databases
from bugbug.tools.code_review.database import (
    EvaluationAction,
    ReviewCommentsDB,
    SuggestionFeedback,
    SuggestionsFeedbackDB,
)

# Data types (backward compatibility)
from bugbug.tools.core.data_types import InlineComment, ReviewRequest

# Exceptions (backward compatibility)
from bugbug.tools.core.exceptions import (
    FileNotInPatchError,
    HunkNotInPatchError,
    LargeDiffError,
    ModelResultError,
)

# Base classes (backward compatibility)
from bugbug.tools.core.platforms.base import ReviewData

# Platforms (backward compatibility)
from bugbug.tools.core.platforms.phabricator import (
    PhabricatorReviewData,
)
from bugbug.tools.core.platforms.swarm import SwarmReviewData

# Legacy compatibility
review_data_classes = {
    "phabricator": PhabricatorReviewData,
    "swarm": SwarmReviewData,
}

__all__ = [
    # Agent
    "CodeReviewTool",
    # Databases
    "EvaluationAction",
    "ReviewCommentsDB",
    "SuggestionFeedback",
    "SuggestionsFeedbackDB",
    # Data types
    "InlineComment",
    "ReviewRequest",
    # Exceptions
    "FileNotInPatchError",
    "HunkNotInPatchError",
    "LargeDiffError",
    "ModelResultError",
    # Base classes
    "ReviewData",
    # Phabricator
    "PhabricatorReviewData",
    # Legacy
    "review_data_classes",
]
