import json
import os

import pytest
from agent_tools import firefox
from agent_tools.firefox import ci_build

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
    monkeypatch.setattr(ci_build.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(ci_build, "_task", lambda _: _task("b-linux-docker-amd", ENV))
    assert ci_build.linux_build_env("T") == ENV


def test_linux_build_env_needs_an_x86_64_host(monkeypatch):
    monkeypatch.setattr(ci_build.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(ci_build, "_task", lambda _: _task("b-linux-docker-amd", ENV))
    assert ci_build.linux_build_env("T") is None


def test_linux_build_env_skips_unsupported_tasks(monkeypatch):
    monkeypatch.setattr(ci_build.platform, "machine", lambda: "x86_64")
    tasks = (
        _task("b-osx-arm64", ENV),
        _task("b-linux-docker-amd", {"MOZ_FETCHES": ENV["MOZ_FETCHES"]}),
        _task(
            "b-linux-docker-amd",
            {**ENV, "MOZ_FETCHES": json.dumps([{**FETCHES[0], "dest": "sdk"}])},
        ),
        _task(
            "b-linux-docker-amd",
            {
                **ENV,
                "MOZ_FETCHES": json.dumps([{**FETCHES[0], "extract": False}]),
            },
        ),
    )
    for task in tasks:
        monkeypatch.setattr(ci_build, "_task", lambda _, task=task: task)
        assert ci_build.linux_build_env("T") is None


async def test_ci_build_setup_supports_reused_workspaces_without_changing_process_env(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ci_build.platform, "machine", lambda: "x86_64")
    monkeypatch.delenv("MOZ_AUTOMATION", raising=False)
    monkeypatch.delenv("MOZ_FETCHES_DIR", raising=False)
    monkeypatch.delenv("PERFHERDER_EXTRA_OPTIONS", raising=False)
    monkeypatch.delenv("DISABLE_CLANG_PLUGIN", raising=False)

    src = tmp_path / "firefox"
    src.mkdir()
    objdir = src / "objdir"
    (objdir / "build" / "clang-plugin").mkdir(parents=True)
    mach_log = tmp_path / "mach.log"
    mach = src / "mach"
    mach.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "--no-interactive" ]; then '
        f'[ ! -e "{src}/.mozconfig" ] || exit 41; fi\n'
        'if [ "$1" = build ]; then\n'
        '  [ "$MOZ_AUTOMATION" = 1 ] || exit 42\n'
        f'  [ "$MOZ_FETCHES_DIR" = "{tmp_path}/fetches" ] || exit 43\n'
        '  [ "$DISABLE_CLANG_PLUGIN" = 1 ] || exit 44\n'
        '  [ "$PERFHERDER_EXTRA_OPTIONS" = x11 ] || exit 45\n'
        "fi\n"
        f'echo "$*" >> "{mach_log}"\n'
    )
    mach.chmod(0o755)
    (src / ".mozconfig").write_text("left by the previous run\n")

    first = firefox.FirefoxContext.from_source_repo(src, objdir=str(objdir))
    first.bootstrap_before_build = True
    await first.configure_ci_build(ENV)
    assert (await firefox.build_firefox(first, target="image"))["success"]
    assert (await firefox.build_firefox(first, target="image"))["success"]

    # A new runtime context models the next local run reusing the same volume.
    second = firefox.FirefoxContext.from_source_repo(src, objdir=str(objdir))
    await second.configure_ci_build(ENV)
    assert (await firefox.build_firefox(second, target="image"))["success"]

    commands = mach_log.read_text().splitlines()
    assert commands == [
        "--no-interactive artifact toolchain --from-task "
        "CLANG:public/build/clang.tar.zst SCCACHE:public/build/sccache.tar.zst",
        "build --quiet pre-export export",
        "build --quiet --allow-subdirectory-build build/clang-plugin image",
        "build --quiet pre-export export",
        "build --quiet --allow-subdirectory-build build/clang-plugin image",
        "--no-interactive artifact toolchain --from-task "
        "CLANG:public/build/clang.tar.zst SCCACHE:public/build/sccache.tar.zst",
        "build --quiet pre-export export",
        "build --quiet --allow-subdirectory-build build/clang-plugin image",
    ]
    assert "MOZ_AUTOMATION" not in os.environ
    assert "MOZ_FETCHES_DIR" not in os.environ
    assert "PERFHERDER_EXTRA_OPTIONS" not in os.environ
    assert "DISABLE_CLANG_PLUGIN" not in os.environ
    assert first.mozconfig.read_text().splitlines() == [
        '. "$topsrcdir/browser/config/mozconfigs/linux64/nightly"',
        f"mk_add_options MOZ_OBJDIR={objdir}",
    ]
    await first.configure_ci_build(None)
    assert first._ci_build_env is None


async def test_failed_ci_setup_does_not_write_a_build_mozconfig(tmp_path, monkeypatch):
    monkeypatch.setattr(ci_build.platform, "machine", lambda: "x86_64")
    mach = tmp_path / "mach"
    mach.write_text("#!/bin/sh\necho toolchain-download-failed >&2\nexit 1\n")
    mach.chmod(0o755)
    ctx = firefox.FirefoxContext.from_source_repo(tmp_path)
    ctx.mozconfig.write_text("left by the previous run\n")

    with pytest.raises(RuntimeError, match="toolchain-download-failed"):
        await ctx.configure_ci_build(ENV)

    assert not ctx.mozconfig.exists()
    assert ctx._ci_build_env is None
