"""Stdio configs for the DevTools MCP servers the agent drives.

The MCP servers are npm packages pinned in ``package.json`` and installed into
the image with ``npm ci`` (see the Dockerfile).
"""

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from autowebcompat_tools.android_browser import (
    CHROME_DEVTOOLS_PORT,
    FIREFOX_MARIONETTE_PORT,
    AndroidBrowser,
)
from claude_agent_sdk.types import McpStdioServerConfig

from .setup_profile import setup_profile


def resolve_bin(bin_name: str) -> str:
    """Resolve an installed MCP server binary to an absolute path."""
    binary = Path("/app/repro") / "node_modules" / ".bin" / bin_name
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


def _build_android_server(
    browser: AndroidBrowser,
    extensions: Sequence[str],
    bin_name: str,
    args: list[str],
) -> McpStdioServerConfig:
    return McpStdioServerConfig(
        command=sys.executable,
        args=[
            "-m",
            "autowebcompat_tools.android_mcp_proxy",
            str(browser),
            json.dumps(list(extensions)),
            resolve_bin(bin_name),
            *args,
        ],
    )


def build_android_firefox_devtools_server(
    browser: AndroidBrowser,
    *,
    enable_privileged_context: bool = False,
    extensions: Sequence[str] = (),
) -> McpStdioServerConfig:
    args = [
        "--connect-existing",
        "--marionette-port",
        str(FIREFOX_MARIONETTE_PORT),
        "--enable-script",
    ]
    if enable_privileged_context:
        args.append("--enable-privileged-context")
    return _build_android_server(browser, extensions, "firefox-devtools-mcp-moz", args)


def build_android_chrome_devtools_server(
    browser: AndroidBrowser,
) -> McpStdioServerConfig:
    return _build_android_server(
        browser,
        (),
        "chrome-devtools-mcp",
        [
            "--browserUrl",
            f"http://127.0.0.1:{CHROME_DEVTOOLS_PORT}",
            "--usageStatistics=false",
            "--performanceCrux=false",
        ],
    )


Browser = Path | AndroidBrowser


def browser_bin(browser: Browser) -> str:
    """BROWSER_BIN for repro scripts: an executable, or an Android debug endpoint."""
    return browser.endpoint if isinstance(browser, AndroidBrowser) else str(browser)


def firefox_server(
    browser: Browser,
    *,
    headless: bool,
    privileged: bool = False,
    extensions: Sequence[str] = (),
) -> McpStdioServerConfig:
    """Firefox DevTools MCP for a desktop binary or an Android browser."""
    if isinstance(browser, AndroidBrowser):
        return build_android_firefox_devtools_server(
            browser, enable_privileged_context=privileged, extensions=extensions
        )
    return build_firefox_devtools_server(
        browser,
        headless=headless,
        enable_privileged_context=privileged,
        profile_path=setup_profile(browser, extensions),
    )


def chrome_server(browser: Browser, *, headless: bool) -> McpStdioServerConfig:
    """Chrome DevTools MCP for a desktop binary or an Android browser."""
    if isinstance(browser, AndroidBrowser):
        return build_android_chrome_devtools_server(browser)
    return build_chrome_devtools_server(browser, headless=headless)
