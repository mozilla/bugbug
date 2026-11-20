"""The `perf` plugin from mozilla/internal-aidev-plugins: the skills and the rankings.

The plugin carries both skills and the internal top-sites rankings
(`data/top_sites.json`) the calculator's --classify-domain reads. The repository
is internal, so the image cannot hold it: the job clones it at start-up, at a
pinned commit, with the repo-scoped GitHub App token the deployment puts in
$GITHUB_TOKEN. The token is removed from the environment once the clone is done,
so the model's shell never sees it.

A local run points $PERF_PLUGIN_DIR at a checkout's `plugins/perf` instead.
Plugin skills are enabled by their plugin-qualified names.
"""

import base64
import os
import subprocess
import tempfile
from pathlib import Path

from claude_agent_sdk.types import SdkPluginConfig
from perf_triage_calculator.perf_triage import TOP_SITES_ENV

PERF_PLUGIN_DIR_ENV = "PERF_PLUGIN_DIR"
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"

PLUGINS_REPO = "https://github.com/mozilla/internal-aidev-plugins"
# Bump to take a newer skill or newer rankings, and re-run the eval when you do.
PLUGINS_REV = "855215814ae71f7dd4012ebdb1cb56b55aa0b431"

TRIAGE_SKILL = "perf:triage-perf-bug"
PROFILER_ANALYSIS_SKILL = "perf:profiler-analysis"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _clone(token: str, dest: Path) -> None:
    # The header goes on the command line, not into .git/config, so the clone
    # keeps no copy of the token.
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    _git("init", "-q", cwd=dest)
    _git(
        "-c",
        f"http.extraHeader=Authorization: Basic {basic}",
        "fetch",
        "-q",
        "--depth",
        "1",
        PLUGINS_REPO,
        PLUGINS_REV,
        cwd=dest,
    )
    _git("checkout", "-q", "FETCH_HEAD", cwd=dest)


def ensure_perf_plugin() -> Path:
    """Make the plugin available, export its paths, and return its directory.

    Sets $PERF_PLUGIN_DIR and, unless already set, $PERF_TRIAGE_TOP_SITES to the
    plugin's rankings. The run stops without them: triaging without the
    rankings changes ratings.
    """
    local = os.environ.get(PERF_PLUGIN_DIR_ENV)
    if local:
        plugin = Path(local)
    else:
        token = os.environ.pop(GITHUB_TOKEN_ENV, None)
        if not token:
            raise RuntimeError(
                f"perf plugin not found: set {GITHUB_TOKEN_ENV} (to clone "
                f"internal-aidev-plugins) or {PERF_PLUGIN_DIR_ENV} (a local checkout)"
            )
        dest = Path(tempfile.mkdtemp(prefix="internal-aidev-plugins-"))
        try:
            _clone(token, dest)
        except subprocess.CalledProcessError as e:
            # git's stderr never contains the header, but keep it out regardless.
            raise RuntimeError(
                f"cloning {PLUGINS_REPO} at {PLUGINS_REV} failed (exit {e.returncode})"
            ) from None
        plugin = dest / "plugins" / "perf"
        os.environ[PERF_PLUGIN_DIR_ENV] = str(plugin)

    skill = plugin / "skills" / "triage-perf-bug" / "SKILL.md"
    top_sites = plugin / "data" / "top_sites.json"
    if not skill.is_file() or not top_sites.is_file():
        raise RuntimeError(
            f"{plugin} is not the perf plugin (no {skill} or {top_sites})"
        )
    os.environ.setdefault(TOP_SITES_ENV, str(top_sites))
    return plugin


def perf_plugin() -> list[SdkPluginConfig]:
    return [{"type": "local", "path": os.environ[PERF_PLUGIN_DIR_ENV]}]
