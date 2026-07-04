from hackbot_runtime.actions.handlers.base import ActionHandler
from hackbot_runtime.actions.handlers.bugzilla_handler import (
    AddAttachmentHandler,
    AddCommentHandler,
    CreateBugHandler,
    UpdateBugHandler,
)
from hackbot_runtime.actions.handlers.phabricator_handler import SubmitPatchHandler

# Maps a recorded action's dotted `type` to the handler that applies it.
# Adding a new action type later is a one-line addition here — the dispatch
# loop (see the apply-run-actions route) never changes.
HANDLERS: dict[str, ActionHandler] = {
    "bugzilla.update_bug": UpdateBugHandler(),
    "bugzilla.add_comment": AddCommentHandler(),
    "bugzilla.add_attachment": AddAttachmentHandler(),
    "bugzilla.create_bug": CreateBugHandler(),
    "phabricator.submit_patch": SubmitPatchHandler(),
}


def get_handler(action_type: str) -> ActionHandler | None:
    return HANDLERS.get(action_type)
