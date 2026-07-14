"""Stdio configs for the DevTools MCP servers the agent drives.

The MCP servers are npm packages pinned in ``package.json`` and installed into
the image with ``npm ci`` (see the Dockerfile).
"""

from __future__ import annotations

from pathlib import Path

from claude_agent_sdk.types import McpStdioServerConfig


def resolve_bin(bin_name: str) -> str:
    """Resolve an installed MCP server binary to an absolute path."""
    binary = Path("/app/node") / "node_modules" / ".bin" / bin_name
    if not binary.exists():
        raise RuntimeError(
            f"MCP server binary not found at {binary}; the image should install "
            f"it with `npm ci` (see the Dockerfile)."
        )
    return str(binary)


def build_firefox_devtools_server(
    firefox_path: Path | None = None,
    *,
    headless: bool = True,
    enable_script: bool = True,
    enable_privileged_context: bool = False,
    profile_path: Path | None = None,
) -> McpStdioServerConfig:
    """Build the stdio config for the Firefox DevTools MCP server.

    Args:
        firefox_path: Firefox binary to drive. When ``None`` the server
            auto-detects an installed Firefox.
        headless: Run Firefox without a visible window (required in
            container/CI environments).
        enable_script: Expose the ``evaluate_script`` tool, which runs
            arbitrary JS in the page context.
        enable_privileged_context: Expose the privileged-context tools
            (``list_extensions``, ``evaluate_privileged_script``, prefs, etc.)
            and set ``MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1`` on the Firefox process.
            Required for the Chrome Mask flow: the agent needs ``list_extensions``
            to resolve the extension's ``moz-extension://<uuid>/`` base URL, and
            navigating to that privileged origin is itself blocked without this.
        profile_path: A pre-built Firefox profile to use as a template (e.g.
            one with the Chrome Mask extension installed). geckodriver copies
            it into a fresh per-session profile, so the template is not
            mutated. When ``None`` the server uses a clean throwaway profile.
    """
    args = []
    if headless:
        args.append("--headless")
    if enable_script:
        args.append("--enable-script")
    if enable_privileged_context:
        args.append("--enable-privileged-context")
    if firefox_path is not None:
        args += ["--firefox-path", str(firefox_path)]
    if profile_path is not None:
        args += ["--profile-path", str(profile_path)]

    command = resolve_bin("firefox-devtools-mcp-moz")
    if enable_privileged_context:
        return McpStdioServerConfig(
            command=command, args=args, env={"MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"}
        )
    return McpStdioServerConfig(command=command, args=args)


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
    args = []
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

    return McpStdioServerConfig(command=resolve_bin("chrome-devtools-mcp"), args=args)
