"""Stdio config for the Firefox DevTools MCP server the agent drives.

The MCP server is an npm package pinned in ``package.json`` and installed into
the image with ``npm ci`` (see the Dockerfile). Unlike autowebcompat-repro this
agent drives the Firefox it just built, not a downloaded release, and needs no
Chrome: the reference behaviour it compares against is the same site with
interventions enabled.
"""

from __future__ import annotations

from pathlib import Path

from claude_agent_sdk.types import McpStdioServerConfig

MCP_INSTALL_DIR = Path("/app/autowebcompat-intervention")


def resolve_bin(bin_name: str) -> str:
    """Resolve an installed MCP server binary to an absolute path."""
    binary = MCP_INSTALL_DIR / "node_modules" / ".bin" / bin_name
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
        firefox_path: Firefox binary to drive -- here, the artifact build's
            ``objdir/dist/bin/firefox``, which carries the agent's intervention
            as a built-in add-on.
        headless: Run Firefox without a visible window.
        enable_script: Expose ``evaluate_script``, which runs JS in the page
            context. Needed to probe whether the breakage is present.
        enable_privileged_context: Expose the privileged-context tools
            (privileged scripts, prefs, extensions) and set
            ``MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1`` on the Firefox process.
        profile_path: A pre-built profile to use as a template. Not needed here
            (the intervention ships inside the build, not as a sideloaded XPI),
            but kept so a caller can pin prefs if required.
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
