"""Smoke tests for the bug-fix agent image.

They check what the agent can do in the image (run Firefox, play audio, build,
run tests), not how the image is put together, so they run inside it. That is
how CI runs them, with BUG_FIX_IMAGE_INSIDE=1. From the host, name a built
image of the ``agent`` target and they run themselves in containers of it:

    docker build --target agent -t bug-fix-agent -f agents/bug-fix/Dockerfile .
    cd agents/bug-fix
    BUG_FIX_IMAGE=bug-fix-agent uv run --extra dev pytest -s tests/test_image.py

They need network access: the evaluate_testcase tests download a Firefox nightly.
Two slower tests bootstrap a Firefox checkout and build it:

- BUG_FIX_IMAGE_SLOW=1: an artifact build, then running tests in it (about 13
  minutes the first time, then 8).
- BUG_FIX_IMAGE_FULL_BUILD=1: a full build with the agent's own mozconfig, then
  running a testcase and tests in it (hours, and a lot of memory).

They check what bootstrap does to a clean home directory, so each needs a fresh
container: from the host, each gets one, with the checkout kept in the
bug-fix-image-test-workspace volume so only the first run pays for the clone.
"""

import array
import asyncio
import base64
import json
import math
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from agent_tools.firefox import FirefoxContext
from agent_tools.firefox.tools import bootstrap_firefox, build_firefox
from agent_tools.firefox.tools.evaluate_testcase import evaluate_testcase
from agent_tools.firefox.tools.js_shell_evaluator import js_shell_evaluator

# Imported before any test bootstraps, as in the agent, so ffpuppet looks
# minidump-stackwalk up before bootstrap installs it.
from ffpuppet.minidump_parser import MinidumpParser
from hackbot_agents.bug_fix.agent import _write_mozconfig

IMAGE = os.environ.get("BUG_FIX_IMAGE")
INSIDE = os.environ.get("BUG_FIX_IMAGE_INSIDE") == "1"
SLOW = os.environ.get("BUG_FIX_IMAGE_SLOW") == "1"
FULL_BUILD = os.environ.get("BUG_FIX_IMAGE_FULL_BUILD") == "1"

pytestmark = pytest.mark.skipif(
    not IMAGE and not INSIDE,
    reason="neither BUG_FIX_IMAGE nor BUG_FIX_IMAGE_INSIDE is set",
)

HOME = Path("/home/agent")
WORKSPACE_VOLUME = "bug-fix-image-test-workspace"
SRC = Path("/workspace/firefox")
PYTEST_VERSION = "9.1.0"

# Where bootstrap puts minidump-stackwalk, and a release to stand in for it in the
# tests that don't bootstrap.
MDSW_DIR = HOME / ".mozbuild" / "minidump-stackwalk"
MDSW_URL = (
    "https://github.com/rust-minidump/rust-minidump/releases/download/v0.27.0/"
    "minidump-stackwalk-x86_64-unknown-linux-gnu.tar.xz"
)
MDSW_SHA256 = "0020324c54cc359596e927ee907204f4c8d4da6718765536edcabaa1622122ad"


def step(message):
    """Say what's happening, for slow tests to show progress (with pytest -s)."""
    print(f"\n=== {time.strftime('%H:%M:%S')} {message}", flush=True)


def _forward(stream, sink, lines):
    for line in stream:
        sink.write(line)
        sink.flush()
        lines.append(line)


def run(argv, *, timeout=600, check=True, **kwargs):
    """Run ``argv``, echoing its output as it comes and returning it too.

    The echo shows live with pytest -s and is part of the report otherwise.
    """
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **kwargs,
    )
    stdout, stderr = [], []
    forwarders = [
        threading.Thread(target=_forward, args=(proc.stdout, sys.stdout, stdout)),
        threading.Thread(target=_forward, args=(proc.stderr, sys.stderr, stderr)),
    ]
    for forwarder in forwarders:
        forwarder.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    finally:
        for forwarder in forwarders:
            forwarder.join()
    result = subprocess.CompletedProcess(
        argv, proc.returncode, "".join(stdout), "".join(stderr)
    )
    if check:
        assert result.returncode == 0, (
            f"{argv} exited with {result.returncode}\n"
            f"stdout:\n{result.stdout[-5000:]}\nstderr:\n{result.stderr[-5000:]}"
        )
    return result


def sh(command, **kwargs):
    """Run a shell command as the agent would, return its stdout."""
    return run(["bash", "-c", command], **kwargs).stdout


def pids_matching(pattern):
    return [
        int(pid)
        for pid in subprocess.run(
            ["pgrep", "-f", "--", pattern], capture_output=True, text=True
        ).stdout.split()
    ]


def proc_status(pid):
    """/proc/<pid>/status as a dict."""
    fields = (line.split(":", 1) for line in Path(f"/proc/{pid}/status").open())
    return {name: value.strip() for name, value in fields}


def test_runs_unprivileged_with_writable_workspace():
    assert sh("whoami").strip() == "agent"
    Path("/workspace/probe").touch()
    Path("/workspace/probe").unlink()


def test_x_display_accepts_clients():
    sh("xset q")


def test_session_bus_answers():
    sh(
        "dbus-send --session --print-reply --dest=org.freedesktop.DBus "
        "/org/freedesktop/DBus org.freedesktop.DBus.ListNames"
    )


PCM = ["--raw", "--format=s16le", "--rate=48000", "--channels=1"]


def record_peak(source, *, while_recording=None):
    """Peak amplitude of a recording of ``source``, optionally while running a command.

    parecord takes a second or two to start delivering audio, and on SIGTERM it
    drops what it hasn't written yet, so record for a while and stop it with SIGINT.
    """
    with tempfile.NamedTemporaryFile() as recording:
        recorder = subprocess.Popen(
            ["timeout", "-s", "INT", "5", "parecord", *PCM, "-d", source],
            stdout=recording,
        )
        if while_recording:
            time.sleep(0.3)
            run(while_recording)
        recorder.wait()
        data = Path(recording.name).read_bytes()
    return max(map(abs, array.array("h", data[: len(data) // 2 * 2])), default=0)


def test_audio_plays(tmp_path):
    # Play a tone the way Firefox does, through the PulseAudio interface, and
    # check it comes out of the (dummy) output device.
    tone = tmp_path / "tone.raw"
    samples = (
        int(16000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(4 * 48000)
    )
    tone.write_bytes(array.array("h", samples).tobytes())
    peak = record_peak("@DEFAULT_MONITOR@", while_recording=["pacat", *PCM, str(tone)])
    assert peak > 8000


def test_pipewire_test_audio_source():
    # Gecko's media tests create audiotestsrc nodes as dummy audio sources.
    run(
        [
            "pw-cli",
            "create-node",
            "adapter",
            "{ factory.name=audiotestsrc node.name=probe-src "
            "media.class=Audio/Source object.linger=true }",
        ]
    )
    # pw-cli exits 0 even when the factory is missing; the node must be there.
    for _ in range(25):
        if "probe-src" in sh("pactl list short sources"):
            break
        time.sleep(0.2)
    assert record_peak("probe-src") > 8000


@pytest.mark.parametrize(
    ("pattern", "family"),
    [
        # What web content asks for, and what fontconfig gives it.
        ("sans-serif", "DejaVu Sans"),
        ("Arial", "Liberation Sans"),
        ("Times New Roman", "Liberation Serif"),
        (":lang=ko", "UnDotum"),
        (":lang=ja", "VL Gothic"),
        ("KacstOne", "KacstOne"),
        ("STIX Math", "STIX Math"),
    ],
)
def test_fonts(pattern, family):
    assert run(["fc-match", pattern, "family"]).stdout.startswith(family)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("hg version -q", "Mercurial"),
        ("git --version", "git version"),
        ("curl --version", "curl"),
        ("zip -v", "Zip"),
        ("unzip -v", "UnZip"),
        ("bzip2 --help 2>&1", "bzip2"),
        ("ffmpeg -version", "ffmpeg version"),
        ("printf 'hay\\nneedle\\n' | rg -n needle", "2:needle"),
        ("echo '{\"a\": [1, 42]}' | jq '.a[1]'", "42"),
        ("sqlite3 :memory: 'select 6 * 7'", "42"),
        ("uv run --no-project python -c 'print(6 * 7)'", "42"),
    ],
)
def test_developer_tools(command, expected):
    assert expected in sh(command)


def test_gdb_backtrace(tmp_path):
    source = tmp_path / "segfault.c"
    source.write_text(
        "void crash_here(int *p) { *p = 42; }\nint main(void) { crash_here(0); }\n"
    )
    run(["gcc", "-g", "-O0", "-o", str(tmp_path / "segfault"), str(source)])
    out = run(
        ["gdb", "-q", "-batch", "-ex", "run", "-ex", "bt", str(tmp_path / "segfault")]
    )
    assert "SIGSEGV" in out.stdout
    assert f"crash_here (p=0x0) at {source}:1" in out.stdout


def test_strace():
    out = run(["strace", "-f", "-e", "trace=openat", "cat", "/etc/hostname"])
    assert 'openat(AT_FDCWD, "/etc/hostname"' in out.stderr


def test_git_commit_and_stash(tmp_path):
    sh(
        f"set -e; git init -q {tmp_path}; cd {tmp_path}; "
        "echo a > f; git add f; git commit -qm init; "
        "echo b > f; git stash -q; git stash pop -q"
    )
    assert sh(f"git -C {tmp_path} log -1 --format='%an <%ae>'").strip() == (
        "Hackbot <hackbot@mozilla.tld>"
    )


def test_python_script_venv_and_pip(tmp_path):
    sh(
        f"set -e; python3 -m venv {tmp_path}/venv; {tmp_path}/venv/bin/pip install -q six; "
        f"{tmp_path}/venv/bin/python -c 'import six, sqlite3, ssl'",
        timeout=300,
    )


@pytest.fixture(scope="module")
def nightly():
    """A Firefox nightly debug build, plus minidump-stackwalk where bootstrap puts it."""
    if os.uname().machine != "x86_64":
        pytest.skip("the minidump-stackwalk stand-in is an x86_64 binary")
    sh("fuzzfetch --debug -n firefox -o /tmp", timeout=900)
    sh(
        f"set -e; mkdir -p {MDSW_DIR}; cd /tmp; curl -fsSL -o mdsw.tar.xz {MDSW_URL}; "
        f"echo '{MDSW_SHA256}  mdsw.tar.xz' | sha256sum -c -; "
        f"tar xf mdsw.tar.xz --strip-components=1 -C {MDSW_DIR} "
        "--wildcards '*/minidump-stackwalk'"
    )
    # ffpuppet looked it up when this module imported it; tell it, as
    # bootstrap_firefox does after installing it.
    MinidumpParser.MDSW_BIN = str(MDSW_DIR / "minidump-stackwalk")
    return Path("/tmp/firefox/firefox")


@pytest.fixture(scope="module")
def nightly_js_shell():
    """The JS shell of a Firefox nightly debug build."""
    sh("fuzzfetch --debug --target js -n js -o /tmp", timeout=900)
    return Path("/tmp/js/dist/bin/js")


def signal_once_running(pattern, sig, *, settle=0):
    """In the background, send ``sig`` to what matches ``pattern`` once it runs."""

    def send():
        for _ in range(60):
            time.sleep(1)
            if pids := pids_matching(pattern):
                time.sleep(settle)
                for pid in pids:
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass
                return

    threading.Thread(target=send, daemon=True).start()


def evaluate(binary, content, *, crash_content=False, **kwargs):
    """Run a testcase with the agent's evaluate_testcase."""
    if crash_content:
        # Content JS cannot crash the browser on purpose, so do it from outside
        # once the page is loaded.
        signal_once_running("contentproc.*tab", signal.SIGSEGV, settle=3)
    result = asyncio.run(
        evaluate_testcase(content, "test.html", Path(binary), **kwargs)
    )
    logs = result.get("logs", {})
    result["output"] = logs.get("stdout", "") + logs.get("stderr", "")
    return result


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


def check_testcase_without_crash(binary):
    result = evaluate(
        binary,
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
    # Web Audio gets a working device.
    assert "audio=running" in result["output"]


def check_testcase_crash(binary):
    result = evaluate(
        binary,
        "<script>setTimeout(() => {}, 100000);</script>",
        crash_content=True,
        timeout=60,
    )
    assert result["crashed"] is True, result["message"]
    crashdata = result["logs"]["crashdata"]
    assert "SIGSEGV" in crashdata
    # Only there if minidump-stackwalk processed the minidump.
    assert "libxul.so" in crashdata


# Media in the formats the web uses, which Firefox decodes itself (VP9, Opus) or
# with the system's ffmpeg (H.264, AAC): element, MIME type, ffmpeg arguments.
VIDEO = "-f lavfi -i testsrc=size=64x48:rate=10:duration=2"
AUDIO = "-f lavfi -i sine=frequency=440:duration=2"
MEDIA = {
    "h264": (
        "video",
        "video/mp4",
        f"{VIDEO} -c:v libx264 -pix_fmt yuv420p -movflags +faststart -f mp4",
    ),
    "vp9": ("video", "video/webm", f"{VIDEO} -c:v libvpx-vp9 -f webm"),
    "aac": ("audio", "audio/mp4", f"{AUDIO} -c:a aac -movflags +faststart -f mp4"),
    "opus": ("audio", "audio/ogg", f"{AUDIO} -c:a libopus -f ogg"),
}

# Exercises what web content commonly relies on and logs what each check got.
FEATURES_JS = """
const results = {};
async function check(name, fn) {
  try {
    results[name] = await Promise.race([
      fn(), new Promise((_, reject) => setTimeout(() => reject("timeout"), 10000))]);
  } catch (e) {
    results[name] = "error: " + e;
  }
}
function play(kind, src) {
  return new Promise((resolve, reject) => {
    const el = document.createElement(kind);
    el.muted = true;
    el.onerror = () => reject(el.error && el.error.message);
    el.ontimeupdate = () => {
      if (el.currentTime > 0.2) {
        resolve(kind == "video" ? el.videoWidth + "x" + el.videoHeight : "played");
      }
    };
    el.src = src;
    document.body.append(el);
    el.play().catch(reject);
  });
}
(async () => {
  await check("canvas", async () => {
    const canvas = Object.assign(document.createElement("canvas"), {width: 4, height: 4});
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "rgb(255, 0, 0)";
    ctx.fillRect(0, 0, 4, 4);
    return [...ctx.getImageData(1, 1, 1, 1).data].join(",");
  });
  await check("webgl", async () => {
    const gl = document.createElement("canvas").getContext("webgl2");
    gl.clearColor(0, 1, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    const pixel = new Uint8Array(4);
    gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    return [...pixel].join(",");
  });
  await check("worker", () => new Promise(resolve => {
    const worker = new Worker(URL.createObjectURL(new Blob(["postMessage(6 * 7)"])));
    worker.onmessage = e => resolve(e.data);
  }));
  await check("wasm", async () => {
    // (func (export "add") (param i32 i32) (result i32) local.get 0 local.get 1 i32.add)
    const add = new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0, 1, 7, 1, 96, 2, 127, 127, 1,
      127, 3, 2, 1, 0, 7, 7, 1, 3, 97, 100, 100, 0, 0, 10, 9, 1, 7, 0, 32, 0, 32, 1, 106, 11]);
    return (await WebAssembly.instantiate(add)).instance.exports.add(2, 3);
  });
  await check("indexeddb", () => new Promise((resolve, reject) => {
    const open = indexedDB.open("probe", 1);
    open.onupgradeneeded = () => open.result.createObjectStore("store");
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const tx = open.result.transaction("store", "readwrite");
      tx.objectStore("store").put("stored", "key");
      tx.oncomplete = () => {
        const get = open.result.transaction("store").objectStore("store").get("key");
        get.onsuccess = () => resolve(get.result);
      };
    };
  }));
  for (const [name, {kind, src}] of Object.entries(MEDIA)) {
    await check(name, () => play(kind, src));
  }
  console.log("FEATURES " + JSON.stringify(results));
  window.close();
})();
"""


def features_page():
    """The FEATURES_JS page, with the media generated by ffmpeg and inlined."""
    media = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, (kind, mime, args) in MEDIA.items():
            out = Path(tmp) / name
            run(["ffmpeg", "-loglevel", "error", *shlex.split(args), str(out)])
            src = f"data:{mime};base64," + base64.b64encode(out.read_bytes()).decode()
            media[name] = {"kind": kind, "src": src}
    return (
        "<!DOCTYPE html><body><script>const MEDIA = "
        + json.dumps(media)
        + ";"
        + FEATURES_JS
        + "</script>"
    )


def check_web_features(binary):
    result = evaluate(binary, features_page(), timeout=60, prefs=ALLOW_AUDIO)
    assert result["crashed"] is False, result["message"]
    line = next(
        (log for log in result["output"].splitlines() if "FEATURES " in log), None
    )
    assert line, result["output"][-5000:]
    # console.log lines look like: console.log: "FEATURES {...}"
    logged = json.loads(line.split("console.log: ", 1)[1])
    assert json.loads(logged.split("FEATURES ", 1)[1]) == {
        "canvas": "255,0,0,255",
        "webgl": "0,255,0,255",
        "worker": 42,
        "wasm": 5,
        "indexeddb": "stored",
        "h264": "64x48",
        "vp9": "64x48",
        "aac": "played",
        "opus": "played",
    }
    # Rendering goes through EGL, as on a desktop, rather than a GLX fallback.
    assert "libEGL missing" not in result["output"]


BLUE_PAGE = '<body style="margin: 0; background: rgb(0, 0, 255)">'


def pixel_at(rgb, width, x, y):
    offset = (y * width + x) * 3
    return tuple(rgb[offset : offset + 3])


def check_draws_on_display(binary, tmp_path):
    """Firefox started from the agent's shell, as `./mach run` would, shows up."""
    page = tmp_path / "blue.html"
    page.write_text(BLUE_PAGE)
    width, height = int(os.environ["SCREEN_WIDTH"]), int(os.environ["SCREEN_HEIGHT"])
    firefox = subprocess.Popen(
        [binary, "--kiosk", "--no-remote", "--profile", tmp_path, page.as_uri()],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            time.sleep(1)
            screen = subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-f", "x11grab"]
                + ["-video_size", f"{width}x{height}", "-i", os.environ["DISPLAY"]]
                + ["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                capture_output=True,
                check=True,
            ).stdout
            r, g, b = pixel_at(screen, width, width // 2, height // 2)
            # Colour management shifts it a little.
            if b > 200 and r < 100 and g < 100:
                break
    finally:
        firefox.terminate()
        firefox.wait()
    assert b > 200 and r < 100 and g < 100, f"middle of the screen is {(r, g, b)}"


def check_js_shell(binary):
    """The agent's evaluate_js_shell runs scripts and notices crashes."""
    ok = asyncio.run(
        js_shell_evaluator(content="print(6 * 7)", js_binary=binary, timeout=60)
    )
    assert ok["crashed"] is False, ok["message"]
    assert ok["logs"]["stdout"].strip() == "42"

    # The tool runs the shell with --fuzzing-safe, which hides crash(), and
    # SpiderMonkey handles SIGSEGV itself; abort it from outside instead.
    signal_once_running("bugbug_js_", signal.SIGABRT)
    crash = asyncio.run(
        js_shell_evaluator(content="for (;;) {}", js_binary=binary, timeout=60)
    )
    assert crash["crashed"] is True, crash["message"]


def check_screenshot(binary, tmp_path):
    """Headless screenshots, for the agent to look at what a page renders."""
    page = tmp_path / "blue.html"
    page.write_text(BLUE_PAGE)
    screenshot = tmp_path / "screenshot.png"
    run(
        [binary, "--headless", "--no-remote", "--profile", tmp_path]
        + ["--screenshot", screenshot, "--window-size", "200,100", page.as_uri()],
        timeout=120,
    )
    rgb = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", screenshot]
        + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
        check=True,
    ).stdout
    assert len(rgb) == 200 * 100 * 3
    assert pixel_at(rgb, 200, 100, 50) == (0, 0, 255)


def content_processes(firefox_pid):
    """Firefox's web content processes, which its fork server starts."""
    processes = []
    for pid in pids_matching("-contentproc -isForBrowser"):
        ancestor = pid
        while ancestor > 1:
            try:
                ancestor = int(proc_status(ancestor)["PPid"])
            except FileNotFoundError:
                break
            if ancestor == firefox_pid:
                processes.append(pid)
                break
    return processes


def check_content_sandbox(binary, tmp_path):
    """Content processes run under the seccomp-bpf sandbox."""
    firefox = subprocess.Popen(
        [binary, "--headless", "--no-remote", "--profile", tmp_path, "about:blank"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(30):
            time.sleep(1)
            if content_processes(firefox.pid):
                break
        time.sleep(3)
        processes = content_processes(firefox.pid)
        assert processes, "no content processes"
        for pid in processes:
            status = proc_status(pid)
            assert (status["Seccomp"], status["NoNewPrivs"]) == ("2", "1"), pid
        # Namespaces depend on the container runtime: Docker's default seccomp
        # profile, for one, doesn't let processes create user namespaces.
        own = [
            ns
            for ns in ("user", "pid", "net")
            if os.readlink(f"/proc/{processes[0]}/ns/{ns}")
            != os.readlink(f"/proc/{firefox.pid}/ns/{ns}")
        ]
        step(f"Content process namespaces of their own: {own or 'none'}")
    finally:
        firefox.terminate()
        firefox.wait()


def record_profile(binary, page, profile, tmp_path, *, features=None):
    """Load ``page`` in Firefox with the Gecko profiler on from startup."""
    env = {
        **os.environ,
        "MOZ_PROFILER_STARTUP": "1",
        "MOZ_PROFILER_SHUTDOWN": str(profile),
    }
    if features:
        env["MOZ_PROFILER_STARTUP_FEATURES"] = features
    (tmp_path / "profile").mkdir(exist_ok=True)
    run(
        [binary, "--headless", "--no-remote", "--profile", tmp_path / "profile"]
        + ["--screenshot", tmp_path / "profiled.png", page.as_uri()],
        env=env,
        timeout=600,
    )
    return json.loads(Path(profile).read_text())


def check_profiler(binary, tmp_path):
    """The Gecko profiler records the parent and child processes."""
    page = tmp_path / "blue.html"
    page.write_text(BLUE_PAGE)
    profile = record_profile(binary, page, tmp_path / "profile.json", tmp_path)
    samples = {t["name"]: len(t["samples"]["data"]) for t in profile["threads"]}
    assert samples.get("GeckoMain", 0) > 0, samples
    assert profile["processes"], "child processes weren't profiled"


BUSY_PAGE = """<script>
function hackbotBusyLoop() {
  let x = 0;
  for (let i = 0; i < 3e8; i++) x += i % 7;
  return x;
}
document.title = hackbotBusyLoop();
</script>"""


def profiler_cli(*args, session, **kwargs):
    return run(["profiler-cli", *args, "--session", session], **kwargs).stdout


def check_profile_analysis(binary, tmp_path, *, native_symbols):
    """Profile a page busy in a JS function, and find the function in it.

    As the Firefox tree's profiler-analysis skill would: with profiler-cli,
    symbolicating with samply. Needs what bootstrap installs (node, samply,
    profiler-edit).
    """
    page = tmp_path / "busy.html"
    page.write_text(BUSY_PAGE)
    profile = tmp_path / "busy-profile.json"
    record_profile(binary, page, profile, tmp_path, features="js,stackwalk,cpu")

    # profiler-edit turns it into the processed format the Profiler loads.
    processed = tmp_path / "busy-processed.json"
    run(["profiler-edit", "-i", profile, "-o", processed])
    assert "shared" in json.loads(processed.read_text())

    session = "image-test"
    profiler_cli("load", "--with-samply", profile, session=session, timeout=600)
    try:
        threads = json.loads(profiler_cli("thread", "list", "--json", session=session))
        if isinstance(threads, dict):
            threads = threads["threads"]
        page_thread = next(
            t
            for t in threads
            if t["name"] == "GeckoMain" and t["processName"].startswith("file://")
        )
        profiler_cli("thread", "select", page_thread["threadHandle"], session=session)
        functions = json.loads(
            profiler_cli(
                "thread", "functions", "--limit", "20", "--json", session=session
            )
        )["functions"]
        assert functions[0]["name"] == "hackbotBusyLoop", functions[:3]
        if native_symbols:
            # A build's own libraries are symbolicated, not left as addresses.
            native = [f for f in functions if f["library"] == "libxul.so"]
            assert native, functions
            assert not [f for f in native if f["name"].startswith("0x")], native
    finally:
        profiler_cli("stop", session=session, check=False)


def test_evaluate_testcase_without_crash(nightly):
    check_testcase_without_crash(nightly)


def test_evaluate_testcase_reports_crash_with_stack(nightly):
    check_testcase_crash(nightly)


def test_firefox_web_features(nightly):
    check_web_features(nightly)


def test_firefox_draws_on_display(nightly, tmp_path):
    check_draws_on_display(nightly, tmp_path)


def test_gdb_runs_firefox(nightly, tmp_path):
    out = run(
        ["gdb", "-q", "-batch", "-ex", "handle SIGSYS SIGPIPE nostop noprint"]
        + ["-ex", "run", "--args", nightly, "--headless", "--no-remote"]
        + ["--profile", tmp_path, "--screenshot", tmp_path / "gdb.png", "about:blank"],
        timeout=300,
    )
    assert "exited normally" in out.stdout


def test_evaluate_js_shell(nightly_js_shell):
    check_js_shell(nightly_js_shell)


def test_firefox_screenshot(nightly, tmp_path):
    check_screenshot(nightly, tmp_path)


def test_firefox_content_sandbox(nightly, tmp_path):
    check_content_sandbox(nightly, tmp_path)


def test_firefox_profiler(nightly, tmp_path):
    check_profiler(nightly, tmp_path)


def checkout_and_bootstrap():
    """Get a Firefox checkout and bootstrap it the way the agent does."""
    step(f"Getting a Firefox checkout in {SRC}")
    # Deep enough for an artifact build to find a recent push CI has built: the
    # tip often hasn't been yet.
    if (SRC / ".git").exists():
        sh(
            f"set -e; cd {SRC}; git fetch -q --depth=50 origin HEAD; "
            "git reset -q --hard FETCH_HEAD; git clean -fdxq",
            timeout=1800,
        )
    else:
        run(
            ["git", "clone", "-q", "--depth=50"]
            + ["https://github.com/mozilla-firefox/firefox.git", SRC],
            timeout=1800,
        )
    _write_mozconfig(FirefoxContext.from_source_repo(SRC))

    # Bootstrap runs unprivileged: everything it would need sudo for is in the image.
    step("Bootstrapping, with the agent's bootstrap_firefox")
    result = asyncio.run(bootstrap_firefox(SRC))
    print(result["stdout"], result["stderr"], sep="\n")
    assert result["success"], result["message"]
    # evaluate_testcase can symbolize crashes from then on, in the same process.
    assert MinidumpParser.MDSW_BIN == str(MDSW_DIR / "minidump-stackwalk")

    # The agent's shell finds what bootstrap installed.
    step("Running the bootstrapped toolchain")
    for command in (
        "rustc --version",
        "cargo --version",
        "clang --version",
        "minidump-stackwalk --version",
        "node --version",
        "treeherder-cli --version",
        "searchfox-cli --version",
        "socorro-cli --version",
        "stmo-cli --version",
        "webspec-index --version",
        "samply --version",
        "profiler-cli --version",
        "pq --version",
        "profiler-edit --help",
    ):
        sh(command)


def mach(*args, mozconfig=None, timeout=900):
    """Run mach in the checkout, with the agent's mozconfig unless told otherwise."""
    env = {**os.environ}
    if mozconfig:
        env["MOZCONFIG"] = str(mozconfig)
    return run(["./mach", *args], cwd=SRC, env=env, timeout=timeout).stdout


@pytest.mark.skipif(not SLOW, reason="BUG_FIX_IMAGE_SLOW is not set")
def test_artifact_build_and_tests(tmp_path):
    checkout_and_bootstrap()

    # The agent's debug + clang-plugin config finds the bootstrapped toolchain.
    step("Configuring with the agent's mozconfig")
    configure = mach("configure", timeout=1800)
    assert f"checking for rustc... {HOME}/.cargo/bin/rustc" in configure
    assert (
        f"checking for the target C compiler... {HOME}/.mozbuild/clang/bin/clang"
        in configure
    )

    # Running tests needs a build; an artifact one is quick.
    artifact = Path("/workspace/mozconfig.artifact")
    artifact.write_text(
        "ac_add_options --enable-artifact-builds\n"
        "mk_add_options MOZ_OBJDIR=/workspace/obj-artifact\n"
    )
    step("Making an artifact build")
    mach("build", mozconfig=artifact, timeout=1800)
    step("Running an xpcshell test")
    mach(
        "xpcshell-test",
        "--sequential",
        "toolkit/modules/tests/xpcshell/test_Log.js",
        mozconfig=artifact,
    )
    step("Running a mochitest")
    mach(
        "mochitest", "--headless", "dom/base/test/test_bug5141.html", mozconfig=artifact
    )
    step("Running a browser-chrome mochitest")
    mach(
        "mochitest",
        "testing/mochitest/tests/browser/browser_pass.js",
        mozconfig=artifact,
    )
    step("Running a web-platform test")
    mach(
        "wpt",
        "--headless",
        "testing/web-platform/tests/dom/nodes/Node-parentNode.html",
        mozconfig=artifact,
    )
    step("Running reftests")
    mach(
        "reftest",
        "--headless",
        "layout/reftests/reftest-sanity/reftest.list",
        mozconfig=artifact,
    )
    step("Running a Marionette test")
    mach(
        "marionette-test",
        "--headless",
        "testing/marionette/harness/marionette_harness/tests/unit/test_title.py",
        mozconfig=artifact,
    )

    step("Profiling a page and analyzing the profile")
    check_profile_analysis(
        "/workspace/obj-artifact/dist/bin/firefox", tmp_path, native_symbols=True
    )

    step("Formatting code")
    probe = SRC / "dom/base/HackbotFormatProbe.cpp"
    probe.write_text("int  probe( int a ){return a+1;}\n")
    try:
        mach("format", probe.relative_to(SRC), mozconfig=artifact)
        formatted = probe.read_text()
    finally:
        probe.unlink()
    assert formatted == "int probe(int a) { return a + 1; }\n"


def build_firefox_showing_progress(fx):
    """The agent's build_firefox, printing mach's log as it goes.

    build_firefox returns mach's output only at the end. Under a coding agent (it
    sets CLAUDECODE) mach also writes it to a log as it goes; follow that.
    """
    done = threading.Event()

    def follow_build_log():
        started = time.time()
        logs = fx.objdir / ".mozbuild" / "logs" / "build"
        while not done.is_set():
            new = [
                p for p in logs.glob("build_log_*.log") if p.stat().st_mtime >= started
            ]
            if new:
                break
            time.sleep(5)
        else:
            return
        with max(new, key=lambda p: p.stat().st_mtime).open() as log:
            while True:
                if line := log.readline():
                    print(line, end="", flush=True)
                elif done.is_set():
                    return
                else:
                    time.sleep(1)

    follower = threading.Thread(target=follow_build_log)
    follower.start()
    try:
        return asyncio.run(build_firefox(fx.source_dir, fx.mozconfig, fx.objdir))
    finally:
        done.set()
        follower.join()


@pytest.mark.skipif(not FULL_BUILD, reason="BUG_FIX_IMAGE_FULL_BUILD is not set")
def test_full_build_and_tests(tmp_path_factory):
    checkout_and_bootstrap()
    fx = FirefoxContext.from_source_repo(SRC)

    # What the agent builds, with its own tool and mozconfig.
    step("Building, with the agent's build_firefox and mozconfig")
    result = build_firefox_showing_progress(fx)
    assert result["success"], result["message"]

    step("Running testcases in the build")
    check_testcase_without_crash(fx.binary)
    check_testcase_crash(fx.binary)
    check_web_features(fx.binary)
    check_draws_on_display(fx.binary, tmp_path_factory.mktemp("display"))
    check_screenshot(fx.binary, tmp_path_factory.mktemp("screenshot"))
    check_content_sandbox(fx.binary, tmp_path_factory.mktemp("sandbox"))
    check_profiler(fx.binary, tmp_path_factory.mktemp("profiler"))

    step("Profiling a page and analyzing the profile")
    check_profile_analysis(
        fx.binary, tmp_path_factory.mktemp("analysis"), native_symbols=True
    )

    step("Running a script in the JS shell")
    check_js_shell(fx.js_binary)

    step("Running an xpcshell test")
    mach("xpcshell-test", "--sequential", "toolkit/modules/tests/xpcshell/test_Log.js")
    step("Running a mochitest")
    mach("mochitest", "--headless", "dom/base/test/test_bug5141.html")
    step("Running a gtest")
    mach("gtest", "Strings.IsChar", timeout=3600)
    step("Running clang-tidy")
    mach("static-analysis", "check", "xpcom/string/nsReadableUtils.cpp", timeout=3600)


# Outside the image, run the tests above in containers of it instead: one for the
# quick tests and one for each slow test, which needs a fresh home directory.
if not INSIDE:
    for _name in [name for name in globals() if name.startswith("test_")]:
        del globals()[_name]

    @pytest.mark.parametrize(
        ("selection", "env"),
        [
            pytest.param("not artifact_build and not full_build", {}, id="quick"),
            pytest.param(
                "artifact_build",
                {"BUG_FIX_IMAGE_SLOW": "1"},
                id="artifact_build",
                marks=pytest.mark.skipif(
                    not SLOW, reason="BUG_FIX_IMAGE_SLOW is not set"
                ),
            ),
            pytest.param(
                "full_build",
                {"BUG_FIX_IMAGE_FULL_BUILD": "1"},
                id="full_build",
                marks=pytest.mark.skipif(
                    not FULL_BUILD, reason="BUG_FIX_IMAGE_FULL_BUILD is not set"
                ),
            ),
        ],
    )
    def test_in_image(selection, env):
        tests = Path(__file__).parent
        env_args = [
            arg for name, value in env.items() for arg in ("-e", f"{name}={value}")
        ]
        workspace = ["-v", f"{WORKSPACE_VOLUME}:/workspace"] if env else []
        # The image's Python has no pip; install pytest with the system one.
        command = (
            "/usr/local/bin/python3 -m pip install --quiet --disable-pip-version-check "
            f"--target /tmp/pydeps pytest=={PYTEST_VERSION} && cd /tmp && "
            "PYTHONPATH=/tmp/pydeps python -m pytest -s -v -rs -p no:cacheprovider "
            f"/tests/{Path(__file__).name} -k {shlex.quote(selection)}"
        )
        run(
            ["docker", "run", "--rm", "-e", "BUG_FIX_IMAGE_INSIDE=1", *env_args]
            + [*workspace, "-v", f"{tests}:/tests:ro", IMAGE, "bash", "-c", command],
            timeout=8 * 3600,
        )
