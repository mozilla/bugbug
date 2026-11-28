from hackbot_agents.build_repair import resolve

GITHUB = "https://github.com/mozilla-firefox/firefox"
PUSHLOG = {
    "pushes": {
        "1": {
            "changesets": [
                {"node": "hgA", "desc": "Bug 1 - a"},
                {"node": "hgB", "desc": "Bug 2 - b"},
            ],
            "git_changesets": ["gitA", "gitB"],
        }
    }
}
LANDO = {
    "git2hg/firefox/gitB": {"hg_hash": "hgB"},
    "hg2git/firefox/hgB": {"git_hash": "gitB"},
}


def _fake_get_json(task):
    def fake(url):
        if "/queue/v1/task/" in url:
            return task
        if "json-pushes?changeset=hgB" in url:
            return PUSHLOG
        return next(v for k, v in LANDO.items() if k in url)

    return fake


def test_resolve_push_maps_a_github_checkout_to_hg(monkeypatch):
    task = {
        "tags": {"project": "autoland"},
        "payload": {"env": {"GECKO_HEAD_REV": "gitB", "GECKO_HEAD_REPOSITORY": GITHUB}},
    }
    monkeypatch.setattr(resolve, "_get_json", _fake_get_json(task))

    push = resolve.resolve_push("TASK")
    assert push.hg_revision == "hgB"
    assert push.git_commits == ["gitB", "gitA"]
    assert push.commit_bugs == {"gitA": 1, "gitB": 2}
    assert resolve.task_push("TASK") == ("autoland", "hgB")


def test_resolve_push_keeps_an_hg_checkout(monkeypatch):
    task = {
        "tags": {"project": "autoland"},
        "payload": {"env": {"GECKO_HEAD_REV": "hgB"}},
    }
    monkeypatch.setattr(resolve, "_get_json", _fake_get_json(task))

    push = resolve.resolve_push("TASK")
    assert push.hg_revision == "hgB"
    assert push.git_commits == ["gitB", "gitA"]
