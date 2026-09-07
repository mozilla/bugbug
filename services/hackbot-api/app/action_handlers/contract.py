"""Values used when applying recorded Hackbot actions."""

# Artifact containing a run's source-code patch.
PATCH_ARTIFACT = "changes/changes.patch"

# Email-body token replaced with the source-code patch.
PATCH_PLACEHOLDER = "{patch}"

# Public URL of the Hackbot UI.
HACKBOT_UI_URL = "https://hackbot.moz.tools"

# Actions that submit source changes to Phabricator.
PATCH_ACTION_TYPES = frozenset({"phabricator.submit_patch", "phabricator.update_patch"})

# Actions that submit source changes to the Try server.
TRY_ACTION_TYPES = frozenset({"try_server.push"})
