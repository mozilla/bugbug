"""Constants used by both ``hackbot-runtime`` and ``hackbot-api``.

Agents record actions with the runtime, and the API applies them later, so both
must use the same values.
"""

SUBMIT_PATCH_ACTION_TYPE = "phabricator.submit_patch"
UPDATE_PATCH_ACTION_TYPE = "phabricator.update_patch"

# Both patch actions submit the working directory's changes as a diff, so
# anything gated on "this run submits a patch" — the diff artifact built by the
# runtime, and the API's check that it was published — has to cover both types.
PATCH_ACTION_TYPES = frozenset({SUBMIT_PATCH_ACTION_TYPE, UPDATE_PATCH_ACTION_TYPE})

# Where the runtime's publish_changes() puts the run's patch; the same key the
# API reads.
PATCH_ARTIFACT = "changes/changes.patch"

# Email-body token substituted with the run's patch when the mail is sent. The
# agent decides whether the body mentions the patch at all, and how it is framed;
# this only says where the text goes.
PATCH_PLACEHOLDER = "{patch}"
