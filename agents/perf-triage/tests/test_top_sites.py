import pytest
from hackbot_agents.perf_triage import top_sites


def test_a_local_file_is_used_as_is(tmp_path, monkeypatch):
    f = tmp_path / "ts.json"
    f.write_text("{}")
    monkeypatch.delenv(top_sites.TOP_SITES_URI_ENV, raising=False)
    monkeypatch.setenv("PERF_TRIAGE_TOP_SITES", str(f))
    assert top_sites.ensure_top_sites() == str(f)


def test_a_gcs_uri_is_downloaded_and_exported(tmp_path, monkeypatch):
    got = {}

    def fake(uri, dest):
        got["uri"] = uri
        dest.write_text("{}")

    monkeypatch.setattr(top_sites, "_download", fake)
    monkeypatch.setenv(top_sites.TOP_SITES_URI_ENV, "gs://bucket/dir/top_sites.json")
    path = top_sites.ensure_top_sites()
    assert got["uri"] == "gs://bucket/dir/top_sites.json"
    import os

    assert os.environ["PERF_TRIAGE_TOP_SITES"] == path


def test_no_rankings_stops_the_run(monkeypatch):
    monkeypatch.delenv(top_sites.TOP_SITES_URI_ENV, raising=False)
    monkeypatch.delenv("PERF_TRIAGE_TOP_SITES", raising=False)
    with pytest.raises(RuntimeError, match="top-sites rankings not found"):
        top_sites.ensure_top_sites()


def test_a_malformed_uri_is_rejected(monkeypatch):
    monkeypatch.setenv(top_sites.TOP_SITES_URI_ENV, "bucket/top_sites.json")
    with pytest.raises(RuntimeError, match="gs://bucket/object"):
        top_sites.ensure_top_sites()
