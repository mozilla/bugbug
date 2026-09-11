from enum import StrEnum

from hackbot_runtime.actions.handlers.base import ActionHandler
from hackbot_runtime.actions.handlers.bugzilla_handler import (
    AddAttachmentHandler,
    AddCommentHandler,
    CreateBugHandler,
    UpdateBugHandler,
)
from hackbot_runtime.actions.handlers.email_handler import SendEmailHandler
from hackbot_runtime.actions.handlers.phabricator_handler import (
    AddCommentHandler as PhabricatorAddCommentHandler,
)
from hackbot_runtime.actions.handlers.phabricator_handler import (
    SubmitPatchHandler,
    UpdatePatchHandler,
)
from hackbot_runtime.actions.handlers.slack_handler import PostMessageHandler
from hackbot_runtime.actions.handlers.testrail_handler import SubmitTestPlanHandler
from hackbot_runtime.actions.handlers.try_server_handler import PushHandler


class ActionType(StrEnum):
    BUGZILLA_UPDATE_BUG = "bugzilla.update_bug"
    BUGZILLA_ADD_COMMENT = "bugzilla.add_comment"
    BUGZILLA_ADD_ATTACHMENT = "bugzilla.add_attachment"
    BUGZILLA_CREATE_BUG = "bugzilla.create_bug"
    PHABRICATOR_SUBMIT_PATCH = "phabricator.submit_patch"
    PHABRICATOR_UPDATE_PATCH = "phabricator.update_patch"
    PHABRICATOR_ADD_COMMENT = "phabricator.add_comment"
    TESTRAIL_SUBMIT_TEST_PLAN = "testrail.submit_test_plan"
    SLACK_POST_MESSAGE = "slack.post_message"
    EMAIL_SEND = "email.send"
    TRY_SERVER_PUSH = "try_server.push"


# Maps a recorded action's dotted `type` to the handler that applies it.
# Adding a new action type later is a one-line addition here — the dispatch
# loop (see the apply-run-actions route) never changes.
HANDLERS: dict[ActionType, ActionHandler] = {
    ActionType.BUGZILLA_UPDATE_BUG: UpdateBugHandler(),
    ActionType.BUGZILLA_ADD_COMMENT: AddCommentHandler(),
    ActionType.BUGZILLA_ADD_ATTACHMENT: AddAttachmentHandler(),
    ActionType.BUGZILLA_CREATE_BUG: CreateBugHandler(),
    ActionType.PHABRICATOR_SUBMIT_PATCH: SubmitPatchHandler(),
    ActionType.PHABRICATOR_UPDATE_PATCH: UpdatePatchHandler(),
    ActionType.PHABRICATOR_ADD_COMMENT: PhabricatorAddCommentHandler(),
    ActionType.TESTRAIL_SUBMIT_TEST_PLAN: SubmitTestPlanHandler(),
    ActionType.SLACK_POST_MESSAGE: PostMessageHandler(),
    ActionType.EMAIL_SEND: SendEmailHandler(),
    ActionType.TRY_SERVER_PUSH: PushHandler(),
}


def get_handler(action_type: ActionType) -> ActionHandler | None:
    return HANDLERS.get(action_type)
