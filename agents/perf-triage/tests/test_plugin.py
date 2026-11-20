import subprocess

import pytest
from hackbot_agents.perf_triage import plugin


def _make_plugin(root):
    p = root / "plugins" / "perf"
    (p / "skills" / "triage-perf-bug").mkdir(parents=True)
    (p / "skills" / "triage-perf-bug" / "SKILL.md").write_text("x")
    (p / "data").mkdir()
    (p / "data" / "top_sites.json").write_text("{}")
    return p


@pytest.fixture(autouse=True)
def _no_local_plugin(monkeypatch):
    monkeypatch.delenv("PERF_PLUGIN_DIR")


def test_a_local_checkout_is_used_and_supplies_the_rankings(tmp_path, monkeypatch):
    p = _make_plugin(tmp_path)
    monkeypatch.setenv("PERF_PLUGIN_DIR", str(p))
    assert plugin.ensure_perf_plugin() == p
    import os

    assert os.environ["PERF_TRIAGE_TOP_SITES"] == str(p / "data" / "top_sites.json")


def test_the_token_clones_and_is_removed_from_the_environment(monkeypatch):
    got = {}

    def fake(token, dest):
        got["token"] = token
        _make_plugin(dest)

    monkeypatch.setattr(plugin, "_clone", fake)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    p = plugin.ensure_perf_plugin()
    import os

    assert got["token"] == "t"
    assert "GITHUB_TOKEN" not in os.environ
    assert os.environ["PERF_PLUGIN_DIR"] == str(p)
    assert plugin.perf_plugin() == [{"type": "local", "path": str(p)}]


def test_a_failed_clone_does_not_echo_the_token(monkeypatch):
    def fail(token, dest):
        raise subprocess.CalledProcessError(128, ["git"], stderr=b"secret-t")

    monkeypatch.setattr(plugin, "_clone", fail)
    monkeypatch.setenv("GITHUB_TOKEN", "secret-t")
    with pytest.raises(RuntimeError) as e:
        plugin.ensure_perf_plugin()
    assert "secret-t" not in str(e.value)


def test_neither_stops_the_run():
    with pytest.raises(RuntimeError, match="perf plugin not found"):
        plugin.ensure_perf_plugin()


def test_a_directory_without_the_rankings_is_rejected(tmp_path, monkeypatch):
    p = _make_plugin(tmp_path)
    (p / "data" / "top_sites.json").unlink()
    monkeypatch.setenv("PERF_PLUGIN_DIR", str(p))
    with pytest.raises(RuntimeError, match="is not the perf plugin"):
        plugin.ensure_perf_plugin()
