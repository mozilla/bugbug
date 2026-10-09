"""Tests for the Firefox tools."""

import os

from agent_tools import firefox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.firefox.tools import bootstrap_firefox
from ffpuppet.minidump_parser import MinidumpParser
from mcp.types import ListToolsRequest


async def _list(server):
    return (
        await server.request_handlers[ListToolsRequest](
            ListToolsRequest(method="tools/list")
        )
    ).root.tools


async def test_exposes_firefox_tools(tmp_path):
    ctx = firefox.FirefoxContext.from_source_repo(tmp_path)
    config = build_sdk_server("firefox", ctx, firefox.TOOLS)
    assert config["type"] == "sdk"
    tools = await _list(config["instance"])
    assert {t.name for t in tools} == {
        "evaluate_testcase",
        "build_firefox",
        "evaluate_js_shell",
        "bootstrap_firefox",
    }


def _fake_tree(tmp_path, exit_code):
    """A source dir whose `./mach` exits with ``exit_code``."""
    src = tmp_path / "firefox"
    src.mkdir()
    mach = src / "mach"
    mach.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    mach.chmod(0o755)
    return src


def _fake_minidump_stackwalk(tmp_path, monkeypatch):
    """Put a minidump-stackwalk on PATH, as bootstrap does."""
    bin_dir = tmp_path / "minidump-stackwalk"
    bin_dir.mkdir()
    mdsw = bin_dir / "minidump-stackwalk"
    mdsw.write_text("#!/bin/sh\n")
    mdsw.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return mdsw


async def test_bootstrap_points_ffpuppet_at_minidump_stackwalk(tmp_path, monkeypatch):
    # ffpuppet looked minidump-stackwalk up at import time, before bootstrap
    # installed it.
    monkeypatch.setattr(MinidumpParser, "MDSW_BIN", None)
    mdsw = _fake_minidump_stackwalk(tmp_path, monkeypatch)

    result = await bootstrap_firefox(_fake_tree(tmp_path, 0))

    assert result["success"]
    assert str(mdsw) == MinidumpParser.MDSW_BIN


async def test_failed_bootstrap_leaves_ffpuppet_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(MinidumpParser, "MDSW_BIN", None)
    _fake_minidump_stackwalk(tmp_path, monkeypatch)

    result = await bootstrap_firefox(_fake_tree(tmp_path, 1))

    assert not result["success"]
    assert MinidumpParser.MDSW_BIN is None


def _recording_tree(tmp_path, tools_exit_code):
    """A source dir whose `./mach` logs its commands; `mach python` exits as told."""
    src = tmp_path / "firefox"
    src.mkdir()
    mach = src / "mach"
    mach.write_text(
        "#!/bin/sh\n"
        f'echo "$1 $2" >> "{tmp_path}/mach.log"\n'
        f'[ "$1" = python ] && exit {tools_exit_code}\n'
        "exit 0\n"
    )
    mach.chmod(0o755)
    return src


async def test_bootstrap_installs_developer_tools(tmp_path):
    result = await bootstrap_firefox(_recording_tree(tmp_path, 0))

    assert result["success"]
    assert result["message"] == "mach bootstrap completed successfully"
    log = (tmp_path / "mach.log").read_text().splitlines()
    assert log[0] == "--no-interactive bootstrap"
    assert log[1].startswith("python ")
    assert log[1].endswith("/mach_python/install_developer_tools.py")


async def test_developer_tools_failure_does_not_fail_bootstrap(tmp_path):
    result = await bootstrap_firefox(_recording_tree(tmp_path, 3))

    assert result["success"]
    assert "installing developer tools failed with exit code 3" in result["message"]


async def test_bootstrap_runs_without_controlling_terminal(tmp_path):
    # So a password prompt (bootstrap runs `su` for system packages) fails
    # instead of waiting on the terminal.
    src = tmp_path / "firefox"
    src.mkdir()
    mach = src / "mach"
    mach.write_text(
        f"#!/bin/sh\nawk '{{print $6}}' /proc/$$/stat >> {tmp_path}/sessions\n"
    )
    mach.chmod(0o755)

    result = await bootstrap_firefox(src)

    assert result["success"]
    sessions = (tmp_path / "sessions").read_text().split()
    assert len(sessions) == 2
    assert str(os.getsid(0)) not in sessions
