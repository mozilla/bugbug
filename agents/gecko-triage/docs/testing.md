# Testing gecko-triage

Four levels, from the cheapest to the closest to production.

| Level           | What it proves                                                                                                                                                | Where                    |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------ |
| Unit tests      | The hooks, the preflight, the state, the team files, the plugin check, the duplicate search, the plan parsing, the /tmp write guard and the attachment reader | any machine, no network  |
| Local dry run   | The model triages real bugs with the real tools and the plugin skills; nothing is posted                                                                      | the host, with `uv`      |
| Image check     | The Dockerfile builds; Node, `profiler-cli`, the plugin, `webspec-index` and `ffmpeg` are in the image; `attachments.py` unpacks files in it                  | Docker, `linux/amd64`    |
| Dev environment | Network access from Cloud Run, and Hackbot applying the actions                                                                                               | Hackbot dev, after merge |

## Unit tests

```sh
uv run --package hackbot-agent-gecko-triage --extra dev pytest agents/gecko-triage/tests
```

## Local dry run

See "Running it" in the agent's `README.md`, the part on the host without Docker. The run writes
`summary.json` with the findings and the recorded actions. Nothing posts, because only
hackbot-api applies actions. The session sets `FX_BUG_TOOLKIT_MODE=hackbot` on the host
too, so the skills run in Hackbot mode as they do in the image.

Get approval for each real bug before a run reads it. Restricted bugs are never used.

## Image check

Cloud Run runs `linux/amd64`. The image needs a static `webspec-index`, and release 0.14.0
has one only for amd64, so an arm64 build fails. Build for amd64:

```sh
docker build --platform linux/amd64 -f agents/gecko-triage/Dockerfile --target agent \
    -t gecko-triage:local .
docker run --rm --platform linux/amd64 --entrypoint bash gecko-triage:local -c \
    'set -euo pipefail \
     && python -m hackbot_agents.gecko_triage.toolkit /opt/fx-bug-toolkit \
     && profiler-cli --version && webspec-index --version \
     && ffmpeg -version | head -n 1 && ffprobe -version | head -n 1'
```

The build itself runs the plugin version check, so a wrong pin fails the build.

Then run the attachment smoke test in the image. It makes three files in `/tmp`: a zip
with a log, an about:memory report (`.json.gz`) and a 2-second test video. It reads each
with `attachments.process_file`. It uses no network.

```sh
docker run --rm -i --platform linux/amd64 --entrypoint python gecko-triage:local - <<'PY'
import gzip, subprocess, zipfile
from pathlib import Path
from hackbot_agents.gecko_triage.attachments import process_file

tmp = Path("/tmp/smoke")
tmp.mkdir()
with zipfile.ZipFile(tmp / "logs.zip", "w") as zf:
    zf.writestr("logs/media.log", "[MediaDecoder] error\n")
(tmp / "memory-report.json.gz").write_bytes(gzip.compress(b'{"reports": []}'))
subprocess.run(
    ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
     "testsrc=duration=2:size=320x240:rate=10", "-pix_fmt", "yuv420p",
     str(tmp / "clip.mp4")],
    check=True,
)
expected = {
    "logs.zip": ["archive", "log"],
    "memory-report.json.gz": ["archive", "json"],
    "clip.mp4": ["video"] + ["video-frame"] * 6,
}
for name, kinds in expected.items():
    result = process_file(tmp / name, tmp / f"{name}.out")
    got = [f["kind"] for f in result["files"]]
    assert got == kinds and "refused" not in result, (name, result)
    print(name, got, result["files"][0].get("media", ""))
print("attachments smoke test: ok")
PY
```

## Dev environment

After merge, `agents/cloudbuild.yaml` builds the images and deploys them to dev and then
stage. The job must first exist in `mozilla/webservices-infra`. The first dev runs check
what the host cannot: Cloud Run reaching profile storage, crash-stats and the spec sites,
and Hackbot posting the comment and setting the fields.
