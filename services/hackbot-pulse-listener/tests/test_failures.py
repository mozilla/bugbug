"""Tests for extracting failing test groups from a task via mozci."""

from types import SimpleNamespace

import pytest
from app import failures

LABEL = "test-linux1804-64/opt-mochitest-browser-chrome-1"
WPT_LABEL = "test-linux1804-64/opt-web-platform-tests-2"


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
    groups = failures.failing_groups("TASK", LABEL)
    assert {g.group for g in groups} == set(fake)
    bf = next(g for g in groups if g.group.endswith("browser.toml"))
    assert bf.test == "browser/base/content/test/bf/browser_a.js"
    assert bf.failure_type == "GENERIC"
    assert next(
        g for g in groups if g.group.endswith("mochitest.ini")
    ).failure_type == ("TIMEOUT")


def test_failing_groups_skips_empty_group(monkeypatch):
    monkeypatch.setattr(failures, "_failure_types", lambda t: {"grp": []})
    assert failures.failing_groups("TASK", LABEL) == []


def test_failing_groups_raises_on_mozci_error(monkeypatch):
    # Must not be reported as "nothing failed": the caller has to tell an
    # unreadable errorsummary apart from a task with no failing groups.
    def boom(task_id):
        raise RuntimeError("mozci could not read the task")

    monkeypatch.setattr(failures, "_failure_types", boom)
    with pytest.raises(RuntimeError):
        failures.failing_groups("TASK", LABEL)


def test_wpt_group_names_are_normalized_to_source_paths(monkeypatch):
    # The errorsummary names wpt groups by URL path; mozci keys its results by
    # source path, and the regression check compares the two.
    fake = {
        "/html/browsers/the-window-object/foo.html": [("foo.html", _ftype("GENERIC"))],
        "/_mozilla/dom/bar.html": [("bar.html", _ftype("GENERIC"))],
    }
    monkeypatch.setattr(failures, "_failure_types", lambda t: fake)
    groups = {g.group for g in failures.failing_groups("TASK", WPT_LABEL)}
    assert groups == {
        "testing/web-platform/tests/html/browsers/the-window-object/foo.html",
        "testing/web-platform/mozilla/tests/dom/bar.html",
    }


def test_wpt_root_group_is_dropped(monkeypatch):
    monkeypatch.setattr(
        failures, "_failure_types", lambda t: {"/": [("x", _ftype("GENERIC"))]}
    )
    assert failures.failing_groups("TASK", WPT_LABEL) == []


def test_non_wpt_group_names_are_untouched(monkeypatch):
    fake = {"dom/base/test/mochitest.ini": [("dom/base/test/a.js", _ftype("GENERIC"))]}
    monkeypatch.setattr(failures, "_failure_types", lambda t: fake)
    (group,) = failures.failing_groups("TASK", LABEL)
    assert group.group == "dom/base/test/mochitest.ini"


def test_unusable_group_names_are_dropped(monkeypatch):
    # mozci drops absolute and Windows-style names from its own results, so a
    # group named that way could never match anything.
    fake = {"Z:\\build\\tests\\mochitest.ini": [("a.js", _ftype("GENERIC"))]}
    monkeypatch.setattr(failures, "_failure_types", lambda t: fake)
    assert failures.failing_groups("TASK", LABEL) == []
