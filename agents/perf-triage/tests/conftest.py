import pytest


@pytest.fixture(autouse=True)
def _perf_plugin_env(monkeypatch):
    """Run every test as the agent runs, after ensure_perf_plugin().

    The plugin path is set, with no token and no rankings override. Going
    through monkeypatch also undoes what ensure_perf_plugin() writes to
    os.environ.
    """
    monkeypatch.setenv("PERF_PLUGIN_DIR", "/plugins/perf")
    for v in ("GITHUB_TOKEN", "PERF_TRIAGE_TOP_SITES"):
        monkeypatch.setenv(v, "")
        monkeypatch.delenv(v)
