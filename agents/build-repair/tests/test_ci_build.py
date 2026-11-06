import json
import os

from agent_tools.firefox import FirefoxContext
from hackbot_agents.build_repair import agent, resolve

FETCHES = [
    {"artifact": "public/build/clang.tar.zst", "extract": True, "task": "CLANG"},
    {"artifact": "public/build/sccache.tar.zst", "extract": True, "task": "SCCACHE"},
]
ENV = {
    "MOZCONFIG": "browser/config/mozconfigs/linux64/nightly",
    "MOZ_FETCHES": json.dumps(FETCHES),
    "PERFHERDER_EXTRA_OPTIONS": "x11",
    "DISABLE_CLANG_PLUGIN": "1",
}


def _task(worker_type, env):
    return {"workerType": worker_type, "payload": {"env": env}}


def test_linux_build_env_takes_linux_build_tasks(monkeypatch):
    monkeypatch.setattr(resolve.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(resolve, "_task", lambda _: _task("b-linux-docker-amd", ENV))
    assert resolve.linux_build_env("T") == ENV


def test_linux_build_env_needs_an_x86_64_host(monkeypatch):
    monkeypatch.setattr(resolve.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(resolve, "_task", lambda _: _task("b-linux-docker-amd", ENV))
    assert resolve.linux_build_env("T") is None


def test_linux_build_env_skips_other_tasks(monkeypatch):
    monkeypatch.setattr(resolve.platform, "machine", lambda: "x86_64")
    for task in (
        _task("b-osx-arm64", ENV),
        _task("b-linux-docker-amd", {"MOZ_FETCHES": ENV["MOZ_FETCHES"]}),
        _task(
            "b-linux-docker-amd",
            {**ENV, "MOZ_FETCHES": json.dumps([{**FETCHES[0], "dest": "sdk"}])},
        ),
    ):
        monkeypatch.setattr(resolve, "_task", lambda _, task=task: task)
        assert resolve.linux_build_env("T") is None


def test_setup_ci_build_fetches_ci_toolchains_and_uses_ci_mozconfig(
    tmp_path, monkeypatch
):
    src = tmp_path / "firefox"
    src.mkdir()
    mach = src / "mach"
    mach.write_text(
        f'#!/bin/sh\n[ -e "{src}/.mozconfig" ] && exit 1\n'
        f'echo "$(pwd -P) $*" > "{tmp_path}/mach.log"\n'
    )
    mach.chmod(0o755)
    fetches = tmp_path / "fetches"
    for var in (
        "MOZ_AUTOMATION",
        "MOZ_FETCHES_DIR",
        "PERFHERDER_EXTRA_OPTIONS",
        "DISABLE_CLANG_PLUGIN",
    ):
        monkeypatch.delenv(var, raising=False)
    fx_ctx = FirefoxContext.from_source_repo(src, objdir=str(tmp_path / "obj-build"))
    fx_ctx.mozconfig.write_text("left by an earlier run\n")

    agent._setup_ci_build(fx_ctx, ENV)

    assert (tmp_path / "mach.log").read_text().split() == [
        str(fetches.resolve()),
        "--no-interactive",
        "artifact",
        "toolchain",
        "--from-task",
        "CLANG:public/build/clang.tar.zst",
        "SCCACHE:public/build/sccache.tar.zst",
    ]
    assert os.environ["MOZ_AUTOMATION"] == "1"
    assert os.environ["MOZ_FETCHES_DIR"] == str(fetches)
    assert os.environ["PERFHERDER_EXTRA_OPTIONS"] == "x11"
    assert os.environ["DISABLE_CLANG_PLUGIN"] == "1"
    assert fx_ctx.mozconfig.read_text().splitlines() == [
        '. "$topsrcdir/browser/config/mozconfigs/linux64/nightly"',
        f"mk_add_options MOZ_OBJDIR={tmp_path / 'obj-build'}",
    ]
