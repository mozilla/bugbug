"""Tool name lists and the webcompat prefs the agent toggles."""

# Tools that can modify the source repo (the intervention files live there).
SOURCE_WRITE_TOOLS = ["Write", "Edit"]


BUGZILLA_READ_TOOLS = [
    "mcp__bugzilla__search_bugs",
    "mcp__bugzilla__get_bugs",
    "mcp__bugzilla__get_bug_comments",
    "mcp__bugzilla__get_bug_attachments",
    "mcp__bugzilla__download_attachment",
]

PHABRICATOR_READ_TOOLS = [
    "mcp__phabricator__get_revision",
    "mcp__phabricator__get_revision_comments",
    "mcp__phabricator__get_revision_diff",
]

FIREFOX_BUILD_TOOLS = ["mcp__firefox__build_firefox"]


DEVTOOLS_TOOLS = [
    "mcp__firefox-devtools__list_pages",
    "mcp__firefox-devtools__new_page",
    "mcp__firefox-devtools__navigate_page",
    "mcp__firefox-devtools__select_page",
    "mcp__firefox-devtools__close_page",
    "mcp__firefox-devtools__take_snapshot",
    "mcp__firefox-devtools__resolve_uid_to_selector",
    "mcp__firefox-devtools__clear_snapshot",
    "mcp__firefox-devtools__click_by_uid",
    "mcp__firefox-devtools__hover_by_uid",
    "mcp__firefox-devtools__fill_by_uid",
    "mcp__firefox-devtools__fill_form_by_uid",
    "mcp__firefox-devtools__list_console_messages",
    "mcp__firefox-devtools__clear_console_messages",
    "mcp__firefox-devtools__list_network_requests",
    "mcp__firefox-devtools__get_network_request",
    "mcp__firefox-devtools__screenshot_page",
    "mcp__firefox-devtools__screenshot_by_uid",
    "mcp__firefox-devtools__evaluate_script",
    "mcp__firefox-devtools__accept_dialog",
    "mcp__firefox-devtools__dismiss_dialog",
    "mcp__firefox-devtools__navigate_history",
    "mcp__firefox-devtools__set_viewport_size",
    "mcp__firefox-devtools__get_firefox_info",
    "mcp__firefox-devtools__get_firefox_output",
    "mcp__firefox-devtools__restart_firefox",
]

INTERVENTION_ACTIONS = [
    "phabricator.submit_patch",
]

INTERVENTIONS_DIR = "browser/extensions/webcompat/data/interventions"

INTERVENTION_TESTS_DIR = "testing/webcompat/interventions/tests"

# An intervention patch may only touch the webcompat add-on and its tests;
# anything else is rejected at submit_patch and drops the patch at the end.
ALLOWED_CHANGE_PATHS = (
    "browser/extensions/webcompat/",
    "testing/webcompat/",
)

# Artifact builds download the binaries of the nearest ancestor commit that was
# built on CI. The tip is sometimes a DONTBUILD push, so increasing depth ensures
# there are built ancestors available.
CHECKOUT_DEPTH = 100
