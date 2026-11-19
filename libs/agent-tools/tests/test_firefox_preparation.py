"""Opt-in bootstrap before the first build."""

import asyncio

import pytest
from agent_tools import firefox


@pytest.fixture
def tree(tmp_path):
    src = tmp_path / "firefox"
    src.mkdir()
    mach = src / "mach"
    mach.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{tmp_path}/mach.log"\n'
        'if [ "$2" = bootstrap ]; then\n'
        f'  [ ! -e "{tmp_path}/fail-bootstrap" ] || exit 23\n'
        "else\n"
        f'  [ ! -e "{tmp_path}/fail-build" ] || exit 2\n'
        "fi\n"
    )
    mach.chmod(0o755)
    (src / ".mozconfig").write_text(
        "ac_add_options --enable-debug\n"
        "ac_add_options --enable-clang-plugin\n"
        "ac_add_options --enable-js-shell\n"
    )
    return src


def commands(tmp_path):
    return (tmp_path / "mach.log").read_text().splitlines()


async def test_first_build_prepares_once_and_keeps_the_mozconfig(tree, tmp_path):
    config = (tree / ".mozconfig").read_bytes()
    ctx = firefox.FirefoxContext.from_source_repo(tree)
    ctx.bootstrap_before_build = True
    assert not (tmp_path / "mach.log").exists()

    results = await asyncio.gather(
        firefox.build_firefox(ctx), firefox.build_firefox(ctx)
    )
    assert all(result["success"] for result in results)
    assert commands(tmp_path) == [
        "--no-interactive bootstrap --application-choice=browser",
        "build --quiet",
        "build --quiet",
    ]
    assert (tree / ".mozconfig").read_bytes() == config


async def test_build_does_not_prepare_without_opt_in(tree, tmp_path):
    ctx = firefox.FirefoxContext.from_source_repo(tree)
    assert (await firefox.build_firefox(ctx))["success"]
    assert commands(tmp_path) == ["build --quiet"]


async def test_explicit_application_selection_avoids_duplicate_native_bootstrap(
    tree, tmp_path
):
    ctx = firefox.FirefoxContext.from_source_repo(tree)
    ctx.bootstrap_before_build = True
    config = ctx.mozconfig.read_bytes()

    result = await firefox.bootstrap_firefox(ctx, application="browser_artifact_mode")
    assert result["success"]
    assert (await firefox.build_firefox(ctx))["success"]
    assert (await firefox.bootstrap_firefox(ctx, application="browser"))["success"]
    assert (await firefox.build_firefox(ctx))["success"]
    assert commands(tmp_path) == [
        "--no-interactive bootstrap --application-choice=browser_artifact_mode",
        "build --quiet",
        "--no-interactive bootstrap --application-choice=browser",
        "build --quiet",
    ]
    assert ctx.mozconfig.read_bytes() == config


async def test_failed_preparation_stops_build_and_can_be_retried(tree, tmp_path):
    ctx = firefox.FirefoxContext.from_source_repo(tree)
    ctx.bootstrap_before_build = True
    config = ctx.mozconfig.read_bytes()
    failure = tmp_path / "fail-bootstrap"
    failure.touch()

    result = await firefox.build_firefox(ctx)
    assert not result["success"]
    assert result["message"] == "mach bootstrap failed with exit code 23"
    assert commands(tmp_path) == [
        "--no-interactive bootstrap --application-choice=browser"
    ]
    assert ctx.mozconfig.read_bytes() == config

    failure.unlink()
    assert (await firefox.build_firefox(ctx))["success"]
    assert commands(tmp_path)[-2:] == [
        "--no-interactive bootstrap --application-choice=browser",
        "build --quiet",
    ]


async def test_source_error_does_not_repeat_preparation(tree, tmp_path):
    ctx = firefox.FirefoxContext.from_source_repo(tree)
    ctx.bootstrap_before_build = True
    (tmp_path / "fail-build").touch()

    for _ in range(2):
        result = await firefox.build_firefox(ctx)
        assert not result["success"]
        assert result["message"] == "Firefox build failed with exit code 2"
    assert commands(tmp_path) == [
        "--no-interactive bootstrap --application-choice=browser",
        "build --quiet",
        "build --quiet",
    ]
