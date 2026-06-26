from __future__ import annotations

from pathlib import Path

from claude_agent_sdk.types import McpStdioServerConfig

PACKAGE = "@mozilla/firefox-devtools-mcp-moz"


def build_devtools_server(
    firefox_path: Path | None = None,
    *,
    headless: bool = True,
    enable_script: bool = True,
    enable_privileged_context: bool = True,
) -> McpStdioServerConfig:
    """Build the stdio config for the Firefox DevTools MCP server."""
    args = [PACKAGE]
    if headless:
        args.append("--headless")
    if enable_script:
        args.append("--enable-script")
    if enable_privileged_context:
        args.append("--enable-privileged-context")
    if firefox_path is not None:
        args += ["--firefox-path", str(firefox_path)]

    return McpStdioServerConfig(
        command="npx",
        args=args,
        env={
            "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1",
            "ENABLE_PRIVILEGED_CONTEXT": "true",
        },
    )
