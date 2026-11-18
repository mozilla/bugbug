"""Smoke tests for the patched Firefox DevTools MCP server (mcp/etp-tools.js).

The ETP tools decide whether a run blocks anything at all, and they are
inserted into a third-party bundle by text anchors, so they are tested against
the real thing: the pinned npm package and a real Firefox. Both need the
network and take a few minutes, so they only run when asked for:

    ETP_SMOKE=1 uv run --frozen --package hackbot-agent-autowebcompat-etp \
        --with pytest pytest -q agents/autowebcompat-etp/tests/test_smoke.py
        installs the pinned package and applies the patch (needs node and npm)

    ETP_SMOKE=1 ETP_SMOKE_FIREFOX=/path/to/firefox uv run ... (as above)
        also drives that Firefox (a Nightly) through the ETP tools

Run it after changing mcp/etp-tools.js or bumping the package version.
"""

import asyncio
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from hackbot_agents.autowebcompat_etp import mcp_servers, preflight
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

AGENT_DIR = Path(__file__).resolve().parents[1]
PACKAGE = "node_modules/@mozilla/firefox-devtools-mcp-moz"
ETP_TOOLS = {
    "set_etp_mode",
    "get_content_blocking_log",
    "wait_for_page_load",
    "screenshot_full_page",
}
# A script every configuration under test should block: it is on the tracker
# lists Strict blocks with, and it is EasyPrivacy's probe in etp-tools.js.
TRACKER_HOST = "www.google-analytics.com"
TRACKER_PAGE = (
    "<!doctype html><title>smoke</title><h1>smoke test</h1>"
    f'<script src="https://{TRACKER_HOST}/analytics.js"></script>'
)
BLOCKED_RE = re.compile(r"STATE_(BLOCKED|REPLACED)_TRACKING_CONTENT")

smoke = pytest.mark.skipif(
    os.environ.get("ETP_SMOKE") != "1", reason="set ETP_SMOKE=1 to run"
)
needs_firefox = pytest.mark.skipif(
    not os.environ.get("ETP_SMOKE_FIREFOX"),
    reason="set ETP_SMOKE_FIREFOX to a Firefox binary to run",
)


def test_patch_targets_the_pinned_version():
    """A version bump has to come with a look at the patch's anchors."""
    pinned = json.loads((AGENT_DIR / "package.json").read_text())["dependencies"][
        "@mozilla/firefox-devtools-mcp-moz"
    ]
    patch = (AGENT_DIR / "mcp" / "patch-mcp.mjs").read_text()
    expected = re.search(r'EXPECTED_VERSION = "([^"]+)"', patch).group(1)
    assert pinned == expected


def run(cmd: list[str], cwd: Path) -> str:
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, f"{' '.join(cmd)} failed:\n{done.stdout}{done.stderr}"
    return done.stdout


@pytest.fixture(scope="session")
def patched_mcp(tmp_path_factory) -> Path:
    """The pinned package, installed and patched as the Dockerfile does it."""
    mcp_dir = tmp_path_factory.mktemp("etp-mcp")
    for name in ("package.json", "package-lock.json"):
        shutil.copy(AGENT_DIR / name, mcp_dir / name)
    shutil.copytree(AGENT_DIR / "mcp", mcp_dir / "mcp")
    run(["npm", "ci", "--omit=dev"], mcp_dir)
    out = run(["node", "mcp/patch-mcp.mjs", PACKAGE], mcp_dir)
    assert "added the ETP tools" in out
    return mcp_dir


@smoke
def test_patch_applies_to_the_pinned_package(patched_mcp):
    bundle = patched_mcp / PACKAGE / "dist.moz" / "index.js"
    # Every anchor matched (the patch exits non-zero otherwise) and the result
    # is still a module Node can parse.
    run(["node", "--check", str(bundle)], patched_mcp)
    text = bundle.read_text()
    assert (AGENT_DIR / "mcp" / "etp-tools.js").read_text() in text
    for tool in ETP_TOOLS:
        assert f'["{tool}", ' in text
    # Patching twice leaves it alone.
    assert "already patched" in run(["node", "mcp/patch-mcp.mjs", PACKAGE], patched_mcp)


class Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        status = 403 if self.path.startswith("/blocked") else 200
        body = TRACKER_PAGE.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def firefox_server(patched_mcp, tmp_path, monkeypatch):
    """The agent's MCP server config, for ETP_SMOKE_FIREFOX and the patched package."""
    firefox = Path(os.environ["ETP_SMOKE_FIREFOX"])
    # The image installs the package in /app/etp; use the one patched here.
    monkeypatch.setattr(
        mcp_servers,
        "resolve_bin",
        lambda name: str(patched_mcp / "node_modules" / ".bin" / name),
    )
    if sys.platform == "darwin":
        # Keep a macOS Firefox out of the user's own profile directory.
        home = tmp_path / "firefox-home"
        home.mkdir()
        monkeypatch.setenv("CFFIXED_USER_HOME", str(home))
    return mcp_servers.build_firefox_devtools_server(
        firefox,
        headless=True,
        content_blocking_log_file=tmp_path / "content-blocking-log.json",
    )


def text_of(result) -> str:
    return preflight.tool_text(result)


async def drive(server, url: str) -> dict[str, str]:
    """Load `url` in each configuration; the content blocking log of each."""
    params = StdioServerParameters(
        command=server["command"],
        args=server.get("args", []),
        env={**os.environ, **server.get("env", {})},
    )
    logs = {}
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = {tool.name for tool in (await session.list_tools()).tools}
        assert tools >= ETP_TOOLS

        async def call(name: str, args: dict) -> str:
            result = await session.call_tool(name, args)
            assert not result.isError, f"{name}({args}): {text_of(result)}"
            return text_of(result)

        for mode in ("strict", "off", "strict/easyprivacy"):
            switched = await call("set_etp_mode", {"mode": mode})
            if mode == "strict/easyprivacy":
                assert "loaded and blocking" in switched
            await call("navigate_page", {"url": url})
            await call("wait_for_page_load", {"timeoutSeconds": 20})
            logs[mode] = await call("get_content_blocking_log", {})
    return logs


@smoke
@needs_firefox
def test_etp_tools_block_in_a_real_firefox(firefox_server, site):
    async def main():
        async with asyncio.timeout(600):
            return await drive(firefox_server, f"{site}/")

    logs = asyncio.run(main())
    # Strict blocks the tracker with its own lists, off loads it, and Strict
    # with EasyPrivacy blocks it again through the content classifier.
    assert TRACKER_HOST in logs["strict"] and BLOCKED_RE.search(logs["strict"])
    assert not BLOCKED_RE.search(logs["off"])
    assert TRACKER_HOST in logs["strict/easyprivacy"]
    assert BLOCKED_RE.search(logs["strict/easyprivacy"])


@smoke
@needs_firefox
def test_preflight_sees_the_http_status_in_a_real_firefox(
    firefox_server, site, tmp_path
):
    async def load(path: str):
        async with asyncio.timeout(300):
            return await preflight.load_page(
                firefox_server, f"{site}{path}", tmp_path / "preflight.png"
            )

    assert asyncio.run(load("/")) is None
    page = asyncio.run(load("/blocked"))
    assert isinstance(page, preflight.BlockedPage)
    assert (page.status, page.failure_reason) == (403, "blocked")
    assert page.screenshot is not None and page.screenshot.is_file()
