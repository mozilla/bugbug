"""Stdio config for the Firefox DevTools MCP server the agent drives.

The MCP server is an npm package pinned in ``package.json`` and installed into
the image with ``npm ci`` (see the Dockerfile).
"""

from pathlib import Path

from claude_agent_sdk.types import McpStdioServerConfig

# WebDriver's recommended preferences point Remote Settings at a dummy server,
# which leaves only the exception list bundled in the build. Use the live server
# so the agent sees the same ETP exceptions as real users.
REMOTE_SETTINGS_SERVER = "https://firefox.settings.services.mozilla.com/v1"

# Nightly blocks trackers with the new content classifier, which ignores the
# skip-URL prefs the agent tests exceptions with. Turning off its pref mirror
# leaves blocking to the classic URL classifier, which release users run and the
# url-classifier-exceptions list targets.
CONTENT_CLASSIFIER_MIRROR_PREF = "privacy.trackingprotection.content.mirror.mode=0"

# The prefs that turn on the two categories of url-classifier-exceptions
# entries. Turning off baseline also turns off convenience, but set both so the
# prefs say what they mean.
ALLOW_LIST_PREFS = (
    "privacy.trackingprotection.allow_list.baseline.enabled",
    "privacy.trackingprotection.allow_list.convenience.enabled",
)


def resolve_bin(bin_name: str) -> str:
    """Resolve an installed MCP server binary to an absolute path."""
    binary = Path("/app/etp") / "node_modules" / ".bin" / bin_name
    if not binary.exists():
        raise RuntimeError(
            f"MCP server binary not found at {binary}; the image should install "
            f"it with `npm ci` (see the Dockerfile)."
        )
    return str(binary)


def build_firefox_devtools_server(
    firefox_path: Path,
    *,
    headless: bool = True,
    content_blocking_log_file: Path | None = None,
    disable_exceptions: bool = False,
) -> McpStdioServerConfig:
    """Build the stdio config for the Firefox DevTools MCP server.

    The privileged tools are always enabled: the agent switches ETP modes and
    tests candidate exceptions at runtime with ``set_etp_mode`` and
    ``set_firefox_prefs``, and reads the content blocking log with
    ``get_content_blocking_log`` (the ETP tools come from mcp/patch-mcp.mjs).

    If ``content_blocking_log_file`` is given, ``get_content_blocking_log``
    also appends every log it returns to that JSON file, under the ETP mode
    last set with ``set_etp_mode``.

    If ``disable_exceptions`` is set, Firefox starts with the ETP exception
    list off, and ``set_etp_mode`` turns it off again after every switch, since
    switching the category resets it.
    """
    args = [
        "--enable-script",
        "--enable-privileged-context",
        "--firefox-path",
        str(firefox_path),
        "--pref",
        f"services.settings.server={REMOTE_SETTINGS_SERVER}",
        "--pref",
        CONTENT_CLASSIFIER_MIRROR_PREF,
    ]
    if disable_exceptions:
        for pref in ALLOW_LIST_PREFS:
            args += ["--pref", f"{pref}=false"]
    if headless:
        args.append("--headless")

    env = {"MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"}
    if disable_exceptions:
        env["ETP_DISABLE_EXCEPTIONS"] = "1"
    if content_blocking_log_file is not None:
        env["ETP_CONTENT_BLOCKING_LOG_FILE"] = str(content_blocking_log_file)

    return McpStdioServerConfig(
        command=resolve_bin("firefox-devtools-mcp-moz"),
        args=args,
        env=env,
    )
