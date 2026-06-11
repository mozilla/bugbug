"""Tests for hackbot.toml parsing into HackbotConfig."""

from pathlib import Path

import pytest
from hackbot_runtime.config import load_config

FULL_TOML = """
[source]
repo_url = "https://example.com/repo.git"
checkout_path = "/workspace/repo"

[firefox]
enabled = true
objdir = "objdir-custom"
"""


def test_load_full_config(tmp_path):
    path = tmp_path / "hackbot.toml"
    path.write_text(FULL_TOML)

    cfg = load_config(path)

    assert cfg.source is not None
    assert cfg.source.repo_url == "https://example.com/repo.git"
    assert cfg.source.checkout_path == Path("/workspace/repo")
    assert cfg.firefox is not None
    assert cfg.firefox.enabled is True
    assert cfg.firefox.objdir == "objdir-custom"


def test_missing_file_raises(tmp_path):
    # load_config is strict; the "no config" fallback lives in discovery
    # (_resolve_config), which never hands a missing path to load_config.
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "does-not-exist.toml")


def test_missing_tables_default_to_none(tmp_path):
    path = tmp_path / "hackbot.toml"
    path.write_text('[source]\nrepo_url = "https://example.com/repo.git"\n')

    cfg = load_config(path)

    assert cfg.source is not None
    # checkout_path falls back to the SourceConfig default.
    assert cfg.source.checkout_path == Path("/workspace/source")
    assert cfg.firefox is None


def test_firefox_defaults(tmp_path):
    path = tmp_path / "hackbot.toml"
    path.write_text("[firefox]\n")

    cfg = load_config(path)

    assert cfg.firefox is not None
    assert cfg.firefox.enabled is True
    assert cfg.firefox.objdir == "objdir-ff-asan"
