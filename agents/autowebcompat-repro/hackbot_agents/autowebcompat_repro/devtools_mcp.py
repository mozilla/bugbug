from __future__ import annotations

from pathlib import Path

from claude_agent_sdk.types import McpStdioServerConfig

PACKAGE = "@mozilla/firefox-devtools-mcp-moz"


def build_devtools_server(
    firefox_path: Path | None = None,
    *,
    headless: bool = True,
    enable_script: bool = True,
    profile_path: Path | None = None,
) -> McpStdioServerConfig:
    """Build the stdio config for the Firefox DevTools MCP server.

    Args:
        firefox_path: Firefox binary to drive. When ``None`` the server
            auto-detects an installed Firefox.
        headless: Run Firefox without a visible window (required in
            container/CI environments).
        enable_script: Expose the ``evaluate_script`` tool, which runs
            arbitrary JS in the page context. Needed to read JS-only state
            such as ``navigator.userAgent`` during issue reproduction. The
            privileged-context tools are intentionally left disabled.
        profile_path: A pre-built Firefox profile to use as a template (e.g.
            one with the Chrome Mask extension installed). geckodriver copies
            it into a fresh per-session profile, so the template is not
            mutated. When ``None`` the server uses a clean throwaway profile.
    """
    args = [PACKAGE]
    if headless:
        args.append("--headless")
    if enable_script:
        args.append("--enable-script")
    if firefox_path is not None:
        args += ["--firefox-path", str(firefox_path)]
    if profile_path is not None:
        args += ["--profile-path", str(profile_path)]

    return McpStdioServerConfig(command="npx", args=args)
