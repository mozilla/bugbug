"""Tests for the apply-side Phabricator action handler.

Mocks the Conduit API calls (`_conduit_request`) so these exercise the
handler's own logic — payload relay, create-vs-update transaction building,
result parsing — without a network call. The handler itself does no git/
subprocess work at all; that happens agent-side (see test_changes.py's
`build_phabricator_diff` tests).
"""

import json

from hackbot_runtime.actions.handlers import ApplyContext, phabricator_handler

_DIFF_PAYLOAD = {
    "changes": [{"currentPath": "file.txt"}],
    "sourceControlBaseRevision": "abc123",
    "sourceControlPath": "/",
    "sourceControlSystem": "git",
    "branch": "HEAD",
}


def _ctx(diff=_DIFF_PAYLOAD, local_commits=None):
    submission = {"diff": diff}
    if local_commits is not None:
        submission["local_commits"] = local_commits

    async def download(key):
        assert key == "changes/phabricator_diff.json"
        return json.dumps(submission).encode()

    return ApplyContext(run_id="run-1", download_artifact=download)


def _fake_conduit(responses):
    calls = []

    def fake(method, **payload):
        calls.append((method, payload))
        return responses[method]

    return fake, calls


async def test_submit_patch_create_success(monkeypatch):
    fake, calls = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-1", "diffid": 1},
            "differential.revision.edit": {"object": {"id": 555, "phid": "PHID-REV-1"}},
        }
    )
    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(phabricator_handler, "_repository_phid", lambda: "PHID-REPO-1")

    result = await phabricator_handler.SubmitPatchHandler().apply(
        {
            "bug_id": 1,
            "revision_id": None,
            "title": "Fix",
            "summary": "s",
            "reviewers": ["alice"],
        },
        _ctx(),
    )

    assert result.status == "applied"
    assert result.result == {
        "revision_id": 555,
        "revision_url": "https://phabricator.services.mozilla.com/D555",
    }

    creatediff_call = next(c for c in calls if c[0] == "differential.creatediff")
    assert creatediff_call[1]["repositoryPHID"] == "PHID-REPO-1"
    assert creatediff_call[1]["changes"] == _DIFF_PAYLOAD["changes"]

    edit_call = next(c for c in calls if c[0] == "differential.revision.edit")
    assert "objectIdentifier" not in edit_call[1]
    transactions = {t["type"]: t["value"] for t in edit_call[1]["transactions"]}
    assert transactions["update"] == "PHID-DIFF-1"
    assert transactions["title"] == "Fix"
    assert transactions["reviewers.add"] == ["alice"]
    assert transactions["bugzilla.bug-id"] == "1"


async def test_submit_patch_sets_local_commits_property(monkeypatch):
    fake, calls = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-9", "diffid": 9},
            "differential.revision.edit": {"object": {"id": 77}},
            "differential.setdiffproperty": {},
        }
    )
    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(phabricator_handler, "_repository_phid", lambda: "PHID-REPO-1")

    # Only the git-derived fields exist in the artifact; summary + message are
    # filled in apply-side, mirroring moz-phab's set_diff_property.
    git_fields = {
        "author": "Hackbot Agent",
        "authorEmail": "hackbot@mozilla.tld",
        "time": 1,
        "commit": "node1",
        "parents": ["base1"],
        "tree": "tree1",
    }
    result = await phabricator_handler.SubmitPatchHandler().apply(
        {
            "bug_id": 5,
            "title": "Fix the thing",
            "summary": "does it",
            "reviewers": ["alice"],
        },
        _ctx(local_commits={"node1": dict(git_fields)}),
    )
    assert result.status == "applied"

    # The property is set AFTER the revision edit, so the message can embed the
    # revision URL (matching moz-phab's ordering).
    methods = [c[0] for c in calls]
    assert methods.index("differential.revision.edit") < methods.index(
        "differential.setdiffproperty"
    )

    prop_call = next(c for c in calls if c[0] == "differential.setdiffproperty")
    assert prop_call[1]["diff_id"] == 9
    assert prop_call[1]["name"] == "local:commits"
    stored = json.loads(prop_call[1]["data"])["node1"]
    # git-derived fields are preserved untouched
    assert stored["author"] == "Hackbot Agent"
    assert stored["tree"] == "tree1"
    assert stored["parents"] == ["base1"]
    # summary is the revision title; message is arc-formatted with the URL
    assert stored["summary"] == "Fix the thing"
    assert stored["message"].startswith("Fix the thing\n\nSummary:\ndoes it")
    assert (
        "Differential Revision: https://phabricator.services.mozilla.com/D77"
        in stored["message"]
    )
    assert "Reviewers: alice" in stored["message"]
    assert "Bug #: 5" in stored["message"]

    # creatediff gets the raw diff payload; local_commits never leaks into it.
    creatediff_call = next(c for c in calls if c[0] == "differential.creatediff")
    assert creatediff_call[1]["changes"] == _DIFF_PAYLOAD["changes"]
    assert "local_commits" not in creatediff_call[1]


async def test_submit_patch_local_commits_fetches_title_on_update(monkeypatch):
    fake, calls = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-1", "diffid": 3},
            "differential.revision.edit": {"object": {"id": 42}},
            "differential.revision.search": {
                "data": [{"fields": {"title": "Existing title", "summary": "old sum"}}]
            },
            "differential.setdiffproperty": {},
        }
    )
    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(phabricator_handler, "_repository_phid", lambda: "PHID-REPO-1")

    result = await phabricator_handler.SubmitPatchHandler().apply(
        {"bug_id": 9, "revision_id": 42},
        _ctx(local_commits={"n": {"author": "A"}}),
    )
    assert result.status == "applied"

    # No title on the action -> fall back to the existing revision's title.
    stored = json.loads(
        next(c for c in calls if c[0] == "differential.setdiffproperty")[1]["data"]
    )["n"]
    assert stored["summary"] == "Existing title"
    assert (
        "Differential Revision: https://phabricator.services.mozilla.com/D42"
        in stored["message"]
    )


async def test_submit_patch_update_sets_object_identifier(monkeypatch):
    fake, calls = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-2", "diffid": 2},
            "differential.revision.edit": {"object": {"id": 12345}},
        }
    )
    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(phabricator_handler, "_repository_phid", lambda: "PHID-REPO-1")

    result = await phabricator_handler.SubmitPatchHandler().apply(
        {"bug_id": 7, "revision_id": 12345}, _ctx()
    )

    assert result.status == "applied"
    assert result.result["revision_id"] == 12345
    edit_call = next(c for c in calls if c[0] == "differential.revision.edit")
    assert edit_call[1]["objectIdentifier"] == 12345


async def test_submit_patch_falls_back_to_given_revision_id_when_edit_omits_object(
    monkeypatch,
):
    fake, _ = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-3"},
            "differential.revision.edit": {"object": {}},
        }
    )
    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(phabricator_handler, "_repository_phid", lambda: "PHID-REPO-1")

    result = await phabricator_handler.SubmitPatchHandler().apply(
        {"bug_id": 7, "revision_id": 999}, _ctx()
    )
    assert result.status == "applied"
    assert result.result["revision_id"] == 999


async def test_submit_patch_missing_artifact_fails():
    async def download(key):
        raise KeyError(key)

    ctx = ApplyContext(run_id="run-1", download_artifact=download)
    result = await phabricator_handler.SubmitPatchHandler().apply({"bug_id": 1}, ctx)
    assert result.status == "failed"


async def test_submit_patch_conduit_error_fails(monkeypatch):
    def fake(method, **payload):
        raise RuntimeError("Conduit error ERR-CONDUIT-CORE: bad request")

    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(phabricator_handler, "_repository_phid", lambda: "PHID-REPO-1")

    result = await phabricator_handler.SubmitPatchHandler().apply(
        {"bug_id": 1, "title": "x"}, _ctx()
    )
    assert result.status == "failed"
    assert "ERR-CONDUIT-CORE" in result.error


def test_repository_phid_prefers_env_var(monkeypatch):
    monkeypatch.setenv("PHABRICATOR_REPOSITORY_PHID", "PHID-FROM-ENV")
    phabricator_handler._repository_phid.cache_clear()
    assert phabricator_handler._repository_phid() == "PHID-FROM-ENV"
    phabricator_handler._repository_phid.cache_clear()


def test_repository_phid_looks_up_by_short_name(monkeypatch):
    monkeypatch.delenv("PHABRICATOR_REPOSITORY_PHID", raising=False)

    def fake(method, **payload):
        assert method == "diffusion.repository.search"
        return {
            "data": [
                {"phid": "PHID-OTHER", "fields": {"shortName": "other-repo"}},
                {"phid": "PHID-MC", "fields": {"shortName": "mozilla-central"}},
            ]
        }

    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    phabricator_handler._repository_phid.cache_clear()
    assert phabricator_handler._repository_phid() == "PHID-MC"
    phabricator_handler._repository_phid.cache_clear()
