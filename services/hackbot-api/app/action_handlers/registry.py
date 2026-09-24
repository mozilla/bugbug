from app.action_handlers.base import ActionHandler
from app.action_handlers.bugzilla_handler import (
    AddAttachmentHandler,
    AddCommentHandler,
    CreateBugHandler,
    UpdateBugHandler,
)
from app.action_handlers.email_handler import SendEmailHandler
from app.action_handlers.phabricator_handler import (
    AddCommentHandler as PhabricatorAddCommentHandler,
)
from app.action_handlers.phabricator_handler import (
    SubmitPatchHandler,
    UpdatePatchHandler,
)
from app.action_handlers.slack_handler import PostMessageHandler
from app.action_handlers.testrail_handler import SubmitTestPlanHandler
from app.action_handlers.try_server_handler import PushHandler

# Actions that submit source changes to Phabricator.
PATCH_ACTION_TYPES = frozenset({"phabricator.submit_patch", "phabricator.update_patch"})

# Maps a recorded action's dotted `type` to the handler that applies it.
# Adding a new action type later is a one-line addition here — the dispatch
# loop (see the apply-run-actions route) never changes.
HANDLERS: dict[str, ActionHandler] = {
    "bugzilla.update_bug": UpdateBugHandler(),
    "bugzilla.add_comment": AddCommentHandler(),
    "bugzilla.add_attachment": AddAttachmentHandler(),
    "bugzilla.create_bug": CreateBugHandler(),
    "phabricator.submit_patch": SubmitPatchHandler(),
    "phabricator.update_patch": UpdatePatchHandler(),
    "phabricator.add_comment": PhabricatorAddCommentHandler(),
    "testrail.submit_test_plan": SubmitTestPlanHandler(),
    "slack.post_message": PostMessageHandler(),
    "email.send": SendEmailHandler(),
    "try_server.push": PushHandler(),
}


def get_handler(action_type: str) -> ActionHandler | None:
    return HANDLERS.get(action_type)
