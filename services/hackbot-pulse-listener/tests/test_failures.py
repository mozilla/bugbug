"""Tests for extracting failing test groups from a task via mozci."""

from types import SimpleNamespace

from app import failures


def _ftype(name):
    return SimpleNamespace(name=name)


def test_failing_groups_maps_mozci_output(monkeypatch):
    fake = {
        "browser/base/content/test/bf/browser.toml": [
            ("browser/base/content/test/bf/browser_a.js", _ftype("GENERIC")),
        ],
        "dom/base/test/mochitest.ini": [
            ("dom/base/test/test_c.js", _ftype("TIMEOUT")),
        ],
    }
    monkeypatch.setattr(failures, "_failure_types", lambda t: fake)
    groups = failures.failing_groups("TASK")
    assert {g.group for g in groups} == set(fake)
    bf = next(g for g in groups if g.group.endswith("browser.toml"))
    assert bf.test == "browser/base/content/test/bf/browser_a.js"
    assert bf.failure_type == "GENERIC"
    assert next(
        g for g in groups if g.group.endswith("mochitest.ini")
    ).failure_type == ("TIMEOUT")


def test_failing_groups_skips_empty_group(monkeypatch):
    monkeypatch.setattr(failures, "_failure_types", lambda t: {"grp": []})
    assert failures.failing_groups("TASK") == []


def test_failing_groups_empty_on_mozci_error(monkeypatch):
    def boom(task_id):
        raise RuntimeError("mozci could not read the task")

    monkeypatch.setattr(failures, "_failure_types", boom)
    assert failures.failing_groups("TASK") == []
