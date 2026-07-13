from __future__ import annotations

from pathlib import Path

from claude_agent_sdk.types import McpStdioServerConfig

PACKAGE = "chrome-devtools-mcp@latest"


def build_chrome_devtools_server(
    chrome_path: Path | None = None,
    *,
    headless: bool = True,
    no_sandbox: bool = True,
) -> McpStdioServerConfig:
    """Build the stdio config for the Chrome DevTools MCP server.

    Args:
        chrome_path: Chrome binary to drive (the Chrome for Testing build from
            ``browser.install_chrome``). When ``None`` the server lets its
            bundled Puppeteer discover a Chrome installation itself.
        headless: Run Chrome without a visible window (required in
            container/CI environments).
        no_sandbox: Pass ``--no-sandbox`` to Chrome. Required when running as an
            unprivileged user inside a container, where Chrome's setuid sandbox
            cannot initialize and the browser otherwise fails to launch.
    """
    args = ["-y", PACKAGE]
    if headless:
        args.append("--headless")
    if chrome_path is not None:
        args += ["--executablePath", str(chrome_path)]

    # Opt out of the MCP server's own data collection: its usage statistics and
    # the CrUX API calls that send performance-trace URLs to Google. This does
    # not touch Chrome's own behavior, only what the MCP server itself reports.
    args += ["--usageStatistics=false", "--performanceCrux=false"]

    if no_sandbox:
        args += ["--chromeArg=--no-sandbox", "--chromeArg=--disable-setuid-sandbox"]

    return McpStdioServerConfig(command="npx", args=args)
