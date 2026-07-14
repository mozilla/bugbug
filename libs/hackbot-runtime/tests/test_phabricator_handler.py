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


def test_revision_title_strips_and_reprefixes():
    rt = phabricator_handler._revision_title
    assert rt("Fix bug", wip=True) == "WIP: Fix bug"
    assert rt("WIP: Fix bug", wip=True) == "WIP: Fix bug"  # not doubled
    assert rt("WIP: Fix bug", wip=False) == "Fix bug"  # prefix stripped


def test_revision_title_never_blank_for_bare_wip_marker():
    # A title that is only a WIP marker must fall back to the original, not go
    # blank (which would be an invalid Phabricator title).
    rt = phabricator_handler._revision_title
    assert rt("WIP:", wip=True) == "WIP: WIP"
    assert rt("WIP", wip=False) == "WIP"


async def test_submit_patch_create_wip_by_default(monkeypatch):
    fake, calls = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-1", "diffid": 1},
            "differential.revision.edit": {"object": {"id": 555, "phid": "PHID-REV-1"}},
        }
    )
    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(phabricator_handler, "_repository_phid", lambda: "PHID-REPO-1")

    result = await phabricator_handler.SubmitPatchHandler().apply(
        {"bug_id": 1, "revision_id": None, "title": "Fix", "summary": "s"},
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
    transactions = {t["type"]: t.get("value") for t in edit_call[1]["transactions"]}
    assert transactions["update"] == "PHID-DIFF-1"
    # WIP by default: title is prefixed, the revision is marked changes-planned,
    # and reviewers are NOT requested.
    assert transactions["title"] == "WIP: Fix"
    assert transactions["plan-changes"] is True
    assert "reviewers.add" not in transactions
    assert transactions["bugzilla.bug-id"] == "1"


async def test_submit_patch_create_non_wip(monkeypatch):
    fake, calls = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-1", "diffid": 1},
            "differential.revision.edit": {"object": {"id": 555}},
        }
    )
    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(phabricator_handler, "_repository_phid", lambda: "PHID-REPO-1")

    await phabricator_handler.SubmitPatchHandler().apply(
        {"bug_id": 1, "title": "Fix", "summary": "s", "wip": False},
        _ctx(),
    )

    edit_call = next(c for c in calls if c[0] == "differential.revision.edit")
    transactions = {t["type"]: t.get("value") for t in edit_call[1]["transactions"]}
    # Not WIP: no WIP prefix and no plan-changes; a brand-new revision needs no
    # request-review (Phabricator auto needs-review). Reviewers are never set.
    assert transactions["title"] == "Fix"
    assert "reviewers.add" not in transactions
    assert "plan-changes" not in transactions
    assert "request-review" not in transactions


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
        {"bug_id": 5, "title": "Fix the thing", "summary": "does it"},
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
    # WIP by default: summary/title carry the WIP prefix and reviewers are empty.
    assert stored["summary"] == "WIP: Fix the thing"
    assert stored["message"].startswith("WIP: Fix the thing\n\nSummary:\ndoes it")
    assert (
        "Differential Revision: https://phabricator.services.mozilla.com/D77"
        in stored["message"]
    )
    assert "Reviewers: \n" in stored["message"]
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

    # No title on the action -> fall back to the existing revision's title, with
    # the WIP prefix applied.
    stored = json.loads(
        next(c for c in calls if c[0] == "differential.setdiffproperty")[1]["data"]
    )["n"]
    assert stored["summary"] == "WIP: Existing title"
    assert (
        "Differential Revision: https://phabricator.services.mozilla.com/D42"
        in stored["message"]
    )


async def test_submit_patch_update_sets_object_identifier(monkeypatch):
    fake, calls = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-2", "diffid": 2},
            "differential.revision.edit": {"object": {"id": 12345}},
            "differential.revision.search": {
                "data": [
                    {"fields": {"title": "T", "status": {"value": "needs-review"}}}
                ]
            },
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


async def test_submit_patch_wip_update_already_changes_planned_uses_second_edit(
    monkeypatch,
):
    fake, calls = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-5", "diffid": 5},
            "differential.revision.edit": {"object": {"id": 50}},
            "differential.revision.search": {
                "data": [
                    {"fields": {"title": "T", "status": {"value": "changes-planned"}}}
                ]
            },
        }
    )
    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(phabricator_handler, "_repository_phid", lambda: "PHID-REPO-1")

    result = await phabricator_handler.SubmitPatchHandler().apply(
        {"bug_id": 3, "revision_id": 50}, _ctx()
    )
    assert result.status == "applied"

    # Revision is already changes-planned, so plan-changes can't ride the main
    # edit (no-op status change errors) — it goes in a second, separate edit.
    edits = [c for c in calls if c[0] == "differential.revision.edit"]
    assert len(edits) == 2
    assert all(t["type"] != "plan-changes" for t in edits[0][1]["transactions"])
    assert edits[1][1]["objectIdentifier"] == 50
    assert edits[1][1]["transactions"] == [{"type": "plan-changes", "value": True}]


async def test_submit_patch_non_wip_update_requests_review(monkeypatch):
    fake, calls = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-6", "diffid": 6},
            "differential.revision.edit": {"object": {"id": 60}},
            "differential.revision.search": {
                "data": [
                    {"fields": {"title": "T", "status": {"value": "changes-planned"}}}
                ]
            },
        }
    )
    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(phabricator_handler, "_repository_phid", lambda: "PHID-REPO-1")

    await phabricator_handler.SubmitPatchHandler().apply(
        {"bug_id": 4, "revision_id": 60, "wip": False}, _ctx()
    )

    # Re-activating an existing non-review revision requests review; not WIP.
    edit_call = next(c for c in calls if c[0] == "differential.revision.edit")
    types = {t["type"] for t in edit_call[1]["transactions"]}
    assert "request-review" in types
    assert "plan-changes" not in types


async def test_submit_patch_falls_back_to_given_revision_id_when_edit_omits_object(
    monkeypatch,
):
    fake, _ = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-3"},
            "differential.revision.edit": {"object": {}},
            "differential.revision.search": {"data": [{"fields": {}}]},
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
