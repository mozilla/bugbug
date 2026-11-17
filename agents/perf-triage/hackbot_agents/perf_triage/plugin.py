"""The `perf` plugin from mozilla/firefox-aidev-plugins, which carries the skills.

The image clones the plugin repository at a pinned commit (Dockerfile); a local
run can point $PERF_PLUGIN_DIR at a checkout's `plugins/perf` instead. Plugin
skills are enabled by their plugin-qualified names.
"""

import os

from claude_agent_sdk.types import SdkPluginConfig

PERF_PLUGIN_DIR_ENV = "PERF_PLUGIN_DIR"
DEFAULT_PERF_PLUGIN_DIR = "/opt/firefox-aidev-plugins/plugins/perf"

TRIAGE_SKILL = "perf:triage-perf-bug"
PROFILER_ANALYSIS_SKILL = "perf:profiler-analysis"


def perf_plugin() -> list[SdkPluginConfig]:
    path = os.environ.get(PERF_PLUGIN_DIR_ENV) or DEFAULT_PERF_PLUGIN_DIR
    return [{"type": "local", "path": path}]
