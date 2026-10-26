"""The fx-bug-toolkit plugin: check that it is present and build the SDK options for it.

A missing plugin must fail the run before the model starts. Without it the subagents
would run with no skill and triage without reading the artifacts.
"""

import json
from pathlib import Path
from typing import Any

# Not in `config.py`, which needs a team: the image build checks the plugin first.
# Bump the Dockerfile's FX_BUG_TOOLKIT_COMMIT with TOOLKIT_VERSION. A local run sets
# FX_BUG_TOOLKIT_DIR to a checkout of that release.
TOOLKIT_PLUGIN = "fx-bug-toolkit"
TOOLKIT_VERSION = "0.7.0"
TOOLKIT_SKILLS = ("analyze-profile", "check-log", "spec-check", "bug-start")
DEFAULT_TOOLKIT_DIR = "/opt/fx-bug-toolkit"

# A path with no INDEX.md, so every skill's wiki step finds no wiki and skips itself.
NO_WIKI = "/nonexistent/firefox-wiki"


class ToolkitMissing(RuntimeError):
    pass


def qualified(skill: str) -> str:
    return f"{TOOLKIT_PLUGIN}:{skill}"


def manifest_version(plugin_dir: Path) -> str | None:
    """Return the plugin's version, or None if ``plugin_dir`` is not fx-bug-toolkit."""
    try:
        manifest = json.loads(
            (plugin_dir / ".claude-plugin" / "plugin.json").read_text()
        )
    except OSError, ValueError:
        return None
    return manifest.get("version") if manifest.get("name") == TOOLKIT_PLUGIN else None


def missing_skills(plugin_dir: Path) -> list[str]:
    """Return the skills in TOOLKIT_SKILLS that ``plugin_dir`` lacks at the pinned version."""
    if manifest_version(plugin_dir) != TOOLKIT_VERSION:
        return list(TOOLKIT_SKILLS)
    return [
        skill
        for skill in TOOLKIT_SKILLS
        if not (plugin_dir / "skills" / skill / "SKILL.md").is_file()
    ]


def require_toolkit(plugin_dir: Path) -> None:
    missing = missing_skills(plugin_dir)
    if missing:
        raise ToolkitMissing(
            f"{TOOLKIT_PLUGIN} {TOOLKIT_VERSION} at {plugin_dir} lacks "
            f"{', '.join(missing)} (found version {manifest_version(plugin_dir)}); "
            "set FX_BUG_TOOLKIT_DIR to a checkout of the pinned tag"
        )


def toolkit_options(plugin_dir: Path) -> dict[str, Any]:
    return {
        "plugins": [{"type": "local", "path": str(plugin_dir)}],
        "skills": [qualified(skill) for skill in TOOLKIT_SKILLS],
        "env": {"WIKI_PATH": NO_WIKI},
    }


if __name__ == "__main__":
    import sys

    require_toolkit(Path(sys.argv[1]))
