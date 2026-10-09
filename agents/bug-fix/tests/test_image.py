"""Smoke tests for the bug-fix agent image.

Skipped unless BUG_FIX_IMAGE names a built image of the ``agent`` target:

    docker build --target agent -t bug-fix-agent -f agents/bug-fix/Dockerfile .
    cd agents/bug-fix
    BUG_FIX_IMAGE=bug-fix-agent uv run --extra dev pytest tests/test_image.py

They need network access: the evaluate_testcase tests download a Firefox nightly.
The test that bootstraps a Firefox checkout and runs mach also needs
BUG_FIX_IMAGE_SLOW=1. It keeps the checkout in the bug-fix-image-test-workspace
volume, so only the first run pays for the clone (about 13 minutes, then 5).
"""

import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import PurePosixPath

import pytest

IMAGE = os.environ.get("BUG_FIX_IMAGE")
SLOW = os.environ.get("BUG_FIX_IMAGE_SLOW") == "1"

pytestmark = pytest.mark.skipif(not IMAGE, reason="BUG_FIX_IMAGE is not set")

HOME = PurePosixPath("/home/agent")
WORKSPACE_VOLUME = "bug-fix-image-test-workspace"

# Where bootstrap puts minidump-stackwalk, and a release to stand in for it in the
# tests that don't bootstrap.
MDSW_DIR = HOME / ".mozbuild" / "minidump-stackwalk"
MDSW_URL = (
    "https://github.com/rust-minidump/rust-minidump/releases/download/v0.27.0/"
    "minidump-stackwalk-x86_64-unknown-linux-gnu.tar.xz"
)
MDSW_SHA256 = "0020324c54cc359596e927ee907204f4c8d4da6718765536edcabaa1622122ad"


def _exec(container, *argv, stdin=None, timeout=600, check=True):
    proc = subprocess.run(
        ["docker", "exec", "-i", container, *argv],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check:
        assert proc.returncode == 0, (
            f"{argv} exited with {proc.returncode}\n"
            f"stdout:\n{proc.stdout[-5000:]}\nstderr:\n{proc.stderr[-5000:]}"
        )
    return proc


def sh(container, command, **kwargs):
    """Run a shell command in the container as the agent would, return stdout."""
    return _exec(container, "bash", "-c", command, **kwargs).stdout


def py(container, code, **kwargs):
    """Run a Python script with the agent's interpreter, return stdout."""
    return _exec(container, "python", "-", stdin=code, **kwargs).stdout


@contextmanager
def _started_container(*docker_args):
    name = subprocess.run(
        ["docker", "run", "-d", "--rm", *docker_args, IMAGE, "sleep", "infinity"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    try:
        # The entrypoint starts the display and audio server before exec'ing the
        # command, but `docker run -d` returns before it gets there.
        for _ in range(50):
            ready = _exec(
                name,
                "sh",
                "-c",
                'xset -display "$DISPLAY" q && pactl info',
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.2)
        yield name
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture(scope="module")
def container():
    with _started_container() as name:
        yield name


@pytest.fixture
def workspace_container():
    """A fresh container, but with /workspace kept across runs.

    The home directory (and so what bootstrap installs) starts empty, as in a real
    run; only the Firefox checkout and build output are reused.
    """
    with _started_container("-v", f"{WORKSPACE_VOLUME}:/workspace") as name:
        yield name


def test_runs_unprivileged(container):
    assert sh(container, "whoami").strip() == "agent"
    sh(container, "touch /workspace/probe && rm /workspace/probe")
    assert sh(container, 'echo "$MOZBUILD_STATE_PATH"').strip() == str(
        HOME / ".mozbuild"
    )


def test_toolchain_dirs_on_path(container):
    path = sh(container, 'echo "$PATH"').strip().split(":")
    for directory in (
        ".cargo/bin",
        ".mozbuild/clang/bin",
        ".mozbuild/minidump-stackwalk",
    ):
        assert str(HOME / directory) in path


def test_entrypoint_starts_display_bus_and_audio(container):
    sh(container, 'xset -display "$DISPLAY" q')
    sh(
        container,
        "dbus-send --session --print-reply --dest=org.freedesktop.DBus "
        "/org/freedesktop/DBus org.freedesktop.DBus.ListNames",
    )
    assert "on PipeWire" in sh(container, "pactl info")


def test_pipewire_audiotestsrc_enabled(container):
    # Gecko's media tests use it as a dummy audio source.
    sh(container, r"grep -E '^\s*audiotestsrc\s*=' /etc/pipewire/pipewire.conf")


@pytest.mark.parametrize(
    "family",
    ["Liberation Sans", "DejaVu Sans", "KacstOne", "STIX", "UnDotum", "VL Gothic"],
)
def test_fonts(container, family):
    assert family in sh(container, "fc-list : family")


@pytest.mark.parametrize(
    "command",
    [
        "hg version -q",
        "git --version",
        "curl --version",
        "zip -v",
        "unzip -v",
        "bzip2 --help",
        "ffmpeg -version",
    ],
)
def test_developer_tools(container, command):
    sh(container, command)


def test_git_commit_and_stash(container):
    sh(
        container,
        "set -e; rm -rf /tmp/repo; git init -q /tmp/repo; cd /tmp/repo; "
        "echo a > f; git add f; git commit -qm init; "
        "echo b > f; git stash -q; git stash pop -q",
    )
    assert sh(container, "git -C /tmp/repo log -1 --format='%an <%ae>'").strip() == (
        "Hackbot <hackbot@mozilla.tld>"
    )


def test_python_script_venv_and_pip(container):
    sh(
        container,
        "set -e; python3 -m venv /tmp/venv; /tmp/venv/bin/pip install -q six; "
        "/tmp/venv/bin/python -c 'import six, sqlite3, ssl'",
        timeout=300,
    )


@pytest.fixture(scope="module")
def firefox(container):
    """A Firefox nightly debug build, plus minidump-stackwalk where bootstrap puts it."""
    if sh(container, "uname -m").strip() != "x86_64":
        pytest.skip("the minidump-stackwalk stand-in is an x86_64 binary")
    sh(container, "fuzzfetch --debug -n firefox -o /tmp", timeout=900)
    sh(
        container,
        f"set -e; mkdir -p {MDSW_DIR}; cd /tmp; curl -fsSL -o mdsw.tar.xz {MDSW_URL}; "
        f"echo '{MDSW_SHA256}  mdsw.tar.xz' | sha256sum -c -; "
        f"tar xf mdsw.tar.xz --strip-components=1 -C {MDSW_DIR} "
        "--wildcards '*/minidump-stackwalk'",
    )
    return "/tmp/firefox/firefox"


EVALUATE = """
import asyncio, json, os, signal, subprocess, sys, threading, time
from pathlib import Path
from agent_tools.firefox.tools.evaluate_testcase import evaluate_testcase

args = json.loads(sys.argv[1])

def crash_content_process():
    # Content JS cannot crash the browser on purpose, so do it from outside once
    # the page is loaded.
    for _ in range(60):
        time.sleep(1)
        pids = subprocess.run(["pgrep", "-f", "contentproc.*tab"],
                              capture_output=True, text=True).stdout.split()
        if pids:
            time.sleep(3)
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGSEGV)
                except ProcessLookupError:
                    pass
            return

if args.pop("crash_content"):
    threading.Thread(target=crash_content_process, daemon=True).start()
result = asyncio.run(evaluate_testcase(firefox_binary=Path(args.pop("binary")), **args))
logs = result.get("logs", {})
print(json.dumps({
    "crashed": result.get("crashed"),
    "message": result.get("message"),
    "output": logs.get("stdout", "") + logs.get("stderr", ""),
    "crashdata": logs.get("crashdata", ""),
}))
"""


def evaluate(container, binary, content, *, crash_content=False, **kwargs):
    args = {
        "binary": binary,
        "content": content,
        "filename": "test.html",
        "crash_content": crash_content,
        **kwargs,
    }
    # The script goes on stdin, not in argv, so the pgrep in it can't match itself.
    out = _exec(
        container, "python", "-", json.dumps(args), stdin=EVALUATE, timeout=300
    ).stdout
    return json.loads(out.strip().splitlines()[-1])


PROBE = """<!DOCTYPE html>
<script>
  const ctx = new AudioContext();
  setTimeout(() => {
    console.log("PROBE ua=" + navigator.userAgent + " audio=" + ctx.state);
    window.close();
  }, 2000);
</script>
<script>throw new Error("PROBE_JS_ERROR");</script>"""

# Without a user gesture autoplay keeps the AudioContext suspended no matter what.
ALLOW_AUDIO = {"media.autoplay.default": 0, "media.autoplay.block-webaudio": False}


def test_evaluate_testcase_without_crash(container, firefox):
    result = evaluate(
        container,
        firefox,
        PROBE,
        timeout=30,
        prefs={**ALLOW_AUDIO, "general.useragent.override": "PROBE_UA"},
    )
    assert result["crashed"] is False, result["message"]
    # Console output and uncaught errors reach the logs the agent reads.
    assert "PROBE ua=" in result["output"]
    assert "PROBE_JS_ERROR" in result["output"]
    # Custom prefs are applied.
    assert "ua=PROBE_UA" in result["output"]
    # PipeWire gives Web Audio a working device.
    assert "audio=running" in result["output"]


def test_evaluate_testcase_reports_crash_with_stack(container, firefox):
    result = evaluate(
        container,
        firefox,
        "<script>setTimeout(() => {}, 100000);</script>",
        crash_content=True,
        timeout=60,
    )
    assert result["crashed"] is True, result["message"]
    assert "SIGSEGV" in result["crashdata"]
    # Only there if minidump-stackwalk processed the minidump.
    assert "libxul.so" in result["crashdata"]


BOOTSTRAP = """
import asyncio, json
from pathlib import Path
# Imported first, as in the agent, so ffpuppet looks up minidump-stackwalk
# before bootstrap installs it.
from ffpuppet.minidump_parser import MinidumpParser
from agent_tools.firefox import FirefoxContext
from agent_tools.firefox.tools import bootstrap_firefox
from hackbot_agents.bug_fix.agent import _write_mozconfig

src = Path("/workspace/firefox")
_write_mozconfig(FirefoxContext.from_source_repo(src))
result = asyncio.run(bootstrap_firefox(src))
print(json.dumps({"success": result["success"], "message": result["message"],
                  "stderr": result["stderr"][-5000:], "mdsw": MinidumpParser.MDSW_BIN}))
"""


@pytest.mark.skipif(not SLOW, reason="BUG_FIX_IMAGE_SLOW is not set")
def test_bootstrap_configure_and_test(workspace_container):
    container = workspace_container
    # Deep enough for the artifact build to find a recent push CI has built:
    # the tip often hasn't been yet.
    sh(
        container,
        "set -e; if [ -d /workspace/firefox/.git ]; then "
        "cd /workspace/firefox && git fetch -q --depth=50 origin HEAD && "
        "git reset -q --hard FETCH_HEAD && git clean -fdxq; "
        "else git clone -q --depth=50 https://github.com/mozilla-firefox/firefox.git "
        "/workspace/firefox; fi",
        timeout=1800,
    )

    # Bootstrap runs unprivileged: everything it would need sudo for is in the image.
    result = json.loads(py(container, BOOTSTRAP, timeout=1800).strip().splitlines()[-1])
    assert result["success"], result
    assert result["mdsw"] == str(MDSW_DIR / "minidump-stackwalk")

    # The agent's debug + clang-plugin config finds the bootstrapped toolchain.
    configure = sh(container, "cd /workspace/firefox && ./mach configure", timeout=1800)
    assert f"checking for rustc... {HOME}/.cargo/bin/rustc" in configure
    assert (
        f"checking for the target C compiler... {HOME}/.mozbuild/clang/bin/clang"
        in (configure)
    )

    # Running tests needs a build, so use an artifact build instead of compiling.
    artifact = (
        "cd /workspace/firefox && export MOZCONFIG=/workspace/mozconfig.artifact && "
    )
    sh(
        container,
        "printf '%s\\n' 'ac_add_options --enable-artifact-builds' "
        "'mk_add_options MOZ_OBJDIR=/workspace/obj-artifact' "
        "> /workspace/mozconfig.artifact",
    )
    sh(container, artifact + "./mach build", timeout=1800)
    sh(
        container,
        artifact
        + "./mach xpcshell-test --sequential toolkit/modules/tests/xpcshell/test_Log.js",
        timeout=900,
    )
    sh(
        container,
        artifact + "./mach mochitest --headless dom/base/test/test_bug5141.html",
        timeout=900,
    )
