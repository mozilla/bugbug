"""Tests for the Firefox tools."""

import os

from agent_tools import firefox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.firefox.tools import bootstrap_firefox
from agent_tools.firefox.tools import build_firefox as run_build
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


def _recording_tree(tmp_path):
    """A source dir whose `./mach` logs its commands."""
    src = tmp_path / "firefox"
    src.mkdir()
    mach = src / "mach"
    mach.write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/mach.log"\n')
    mach.chmod(0o755)
    return src


async def test_bootstrap_runs_non_interactively(tmp_path):
    result = await bootstrap_firefox(_recording_tree(tmp_path))

    assert result["success"]
    assert result["message"] == "mach bootstrap completed successfully"
    assert (tmp_path / "mach.log").read_text().splitlines() == [
        "--no-interactive bootstrap --application-choice=browser"
    ]


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
    assert len(sessions) == 1
    assert str(os.getsid(0)) not in sessions


async def test_bootstrap_for_android(tmp_path, monkeypatch):
    src = _recording_tree(tmp_path)
    mozbuild = tmp_path / "mozbuild"
    for path in (
        "jdk/jdk-17.0.9+1",
        "jdk/jdk-17.0.20.1+1",
        "jdk/mozboot",
        "android-sdk-linux/build-tools/36.1.0",
        "android-sdk-linux/build-tools/37.0.0",
        "android-sdk-linux/cmdline-tools/23.0",
        "android-sdk-linux/cmdline-tools/mozboot",
    ):
        (mozbuild / path).mkdir(parents=True)
    monkeypatch.setenv("MOZBUILD_STATE_PATH", str(mozbuild))

    result = await bootstrap_firefox(src, "mobile_android")

    assert result["success"]
    assert (tmp_path / "mach.log").read_text().splitlines() == [
        "--no-interactive bootstrap --application-choice=mobile_android"
    ]
    # The newest of each, for PATH to point at.
    assert os.readlink(mozbuild / "jdk/current") == "jdk-17.0.20.1+1"
    sdk = mozbuild / "android-sdk-linux"
    assert os.readlink(sdk / "build-tools/latest") == "37.0.0"
    assert os.readlink(sdk / "cmdline-tools/latest") == "23.0"


async def test_bootstrap_rejects_unknown_application(tmp_path):
    result = await bootstrap_firefox(_recording_tree(tmp_path), "ios")

    assert not result["success"]
    assert "Unknown application 'ios'" in result["message"]
    assert not (tmp_path / "mach.log").exists()


async def test_bootstrap_tool_offers_android(tmp_path):
    ctx = firefox.FirefoxContext.from_source_repo(tmp_path)
    tools = await _list(build_sdk_server("firefox", ctx, firefox.TOOLS)["instance"])
    bootstrap = next(t for t in tools if t.name == "bootstrap_firefox")
    assert (
        "mobile_android" in bootstrap.inputSchema["properties"]["application"]["enum"]
    )


def _mach_logging_mozconfig(tmp_path, bootstrap_exit_code=0):
    """A source dir whose `./mach` logs the MOZCONFIG it runs with."""
    src = tmp_path / "firefox"
    src.mkdir()
    mach = src / "mach"
    mach.write_text(
        "#!/bin/sh\n"
        f'echo "$MOZCONFIG $1 $2" >> "{tmp_path}/mach.log"\n'
        f'[ "$2" = bootstrap ] && exit {bootstrap_exit_code}\n'
        "exit 0\n"
    )
    mach.chmod(0o755)
    (src / ".mozconfig").write_text("ac_add_options --enable-debug\n")
    return src


async def test_android_bootstrap_switches_builds_to_android(tmp_path, monkeypatch):
    monkeypatch.setenv("MOZBUILD_STATE_PATH", str(tmp_path / "mozbuild"))
    src = _mach_logging_mozconfig(tmp_path)
    ctx = firefox.FirefoxContext.from_source_repo(src)

    result = await firefox.bootstrap_firefox(
        ctx, application="mobile_android_artifact_mode"
    )

    assert result["success"]
    android = src / ".mozconfig-android"
    assert f"MOZCONFIG={android}" in result["message"]
    assert android.read_text().splitlines()[2:] == [
        "ac_add_options --enable-project=mobile/android",
        "ac_add_options --enable-artifact-builds",
        f"mk_add_options MOZ_OBJDIR={src / 'objdir-android-artifact'}",
    ]
    assert ctx.build_config() == (android, src / "objdir-android-artifact")

    result = await firefox.build_firefox(ctx)

    assert result["success"]
    assert result["build_dir"] == str(src / "objdir-android-artifact")
    assert (tmp_path / "mach.log").read_text().splitlines()[
        -1
    ] == f"{android} build --quiet"

    # Bootstrapping for desktop goes back to the agent's own mozconfig.
    await firefox.bootstrap_firefox(ctx, application="browser")
    await firefox.build_firefox(ctx)

    assert ctx.build_config() == (src / ".mozconfig", src / "objdir-ff-asan")
    assert (tmp_path / "mach.log").read_text().splitlines()[-1] == (
        f"{src / '.mozconfig'} build --quiet"
    )


async def test_full_android_build_gets_its_own_objdir(tmp_path, monkeypatch):
    monkeypatch.setenv("MOZBUILD_STATE_PATH", str(tmp_path / "mozbuild"))
    src = _mach_logging_mozconfig(tmp_path)
    ctx = firefox.FirefoxContext.from_source_repo(src)

    await firefox.bootstrap_firefox(ctx, application="mobile_android")

    android = (src / ".mozconfig-android").read_text()
    assert "--enable-artifact-builds" not in android
    assert f"MOZ_OBJDIR={src / 'objdir-android'}\n" in android


async def test_failed_android_bootstrap_keeps_building_desktop(tmp_path):
    src = _mach_logging_mozconfig(tmp_path, bootstrap_exit_code=1)
    ctx = firefox.FirefoxContext.from_source_repo(src)

    result = await firefox.bootstrap_firefox(ctx, application="mobile_android")

    assert not result["success"]
    assert not (src / ".mozconfig-android").exists()
    assert ctx.build_config() == (src / ".mozconfig", src / "objdir-ff-asan")


async def test_build_target_compiles_the_directory_after_its_prerequisites(tmp_path):
    src = tmp_path / "firefox"
    src.mkdir()
    mach = src / "mach"
    mach.write_text(f'#!/bin/sh\necho "$*" >> "{tmp_path}/mach.log"\n')
    mach.chmod(0o755)
    (src / ".mozconfig").write_text("")
    objdir = tmp_path / "obj"
    (objdir / "build" / "clang-plugin").mkdir(parents=True)

    result = await run_build(src, src / ".mozconfig", objdir, target="image")

    assert result["success"]
    assert (tmp_path / "mach.log").read_text().splitlines() == [
        "build --quiet pre-export export",
        "build --quiet --allow-subdirectory-build build/clang-plugin image",
    ]
