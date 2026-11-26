"""The API and the runtime must agree on the values they share.

The runtime records actions and files. The API reads them later.
These tests make sure both use the same names.
"""

from app.action_handlers import email_handler
from app.action_handlers.registry import HANDLERS, PATCH_ACTION_TYPES
from app.routers import runs
from hackbot_runtime import changes
from hackbot_runtime.actions import (
    agents,
    bugzilla,
    email,
    feedback,
    github,
    phabricator,
    slack,
    testrail,
    try_server,
)


def test_patch_action_types_match_runtime():
    assert PATCH_ACTION_TYPES == (
        phabricator.PATCH_ACTION_TYPES | github.PATCH_ACTION_TYPES
    )


def test_patch_artifact_matches_runtime():
    assert email_handler._PATCH_ARTIFACT == changes.PATCH_ARTIFACT
    assert runs._PATCH_ARTIFACT == changes.PATCH_ARTIFACT


def test_patch_placeholder_matches_runtime():
    assert email_handler.PATCH_PLACEHOLDER == email.PATCH_PLACEHOLDER


def test_action_types_match_handlers():
    runtime_action_types = {
        agents.ACTION_TYPE,
        bugzilla.ADD_ATTACHMENT_ACTION_TYPE,
        bugzilla.ADD_COMMENT_ACTION_TYPE,
        bugzilla.CREATE_BUG_ACTION_TYPE,
        bugzilla.UPDATE_BUG_ACTION_TYPE,
        email.ACTION_TYPE,
        feedback.ACTION_TYPE,
        github.ADD_COMMENT_ACTION_TYPE,
        github.ADD_INLINE_COMMENTS_ACTION_TYPE,
        github.SUBMIT_PATCH_ACTION_TYPE,
        phabricator.ADD_COMMENT_ACTION_TYPE,
        phabricator.SUBMIT_PATCH_ACTION_TYPE,
        phabricator.UPDATE_PATCH_ACTION_TYPE,
        slack.ACTION_TYPE,
        testrail.ACTION_TYPE,
        try_server.TRY_PUSH_ACTION_TYPE,
    }
    assert runtime_action_types == HANDLERS.keys()
