# Bugzilla MCP tool names as exposed to the agent (mcp__<server>__<tool>).
BUGZILLA_READ_TOOLS = [
    "mcp__bugzilla__search_bugs",
    "mcp__bugzilla__get_bugs",
    "mcp__bugzilla__get_bug_comments",
    "mcp__bugzilla__get_bug_attachments",
    "mcp__bugzilla__download_attachment",
]

# Firefox DevTools MCP tools (@mozilla/firefox-devtools-mcp-moz), exposed under
# the "firefox-devtools" server name. Web-compat reproduction subset: page
# navigation, accessibility snapshots + UID-based interaction, console/network
# inspection, screenshots, and scripted DOM probing (evaluate_script needs
# --enable-script).
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
    "mcp__firefox-devtools__drag_by_uid_to_uid",
    "mcp__firefox-devtools__upload_file_by_uid",
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
]

# Chrome DevTools MCP tools (chrome-devtools-mcp), exposed under the
# "chrome-devtools" server name. Web-compat reproduction subset mirroring the
# Firefox list: page navigation, accessibility snapshots + UID-based
# interaction, console/network inspection, screenshots, and scripted DOM
# probing (evaluate_script).
CHROME_DEVTOOLS_TOOLS = [
    "mcp__chrome-devtools__list_pages",
    "mcp__chrome-devtools__new_page",
    "mcp__chrome-devtools__navigate_page",
    "mcp__chrome-devtools__select_page",
    "mcp__chrome-devtools__close_page",
    "mcp__chrome-devtools__take_snapshot",
    "mcp__chrome-devtools__click",
    "mcp__chrome-devtools__hover",
    "mcp__chrome-devtools__fill",
    "mcp__chrome-devtools__fill_form",
    "mcp__chrome-devtools__drag",
    "mcp__chrome-devtools__upload_file",
    "mcp__chrome-devtools__list_console_messages",
    "mcp__chrome-devtools__list_network_requests",
    "mcp__chrome-devtools__get_network_request",
    "mcp__chrome-devtools__take_screenshot",
    "mcp__chrome-devtools__evaluate_script",
    "mcp__chrome-devtools__handle_dialog",
    "mcp__chrome-devtools__wait_for",
    "mcp__chrome-devtools__resize_page",
]
