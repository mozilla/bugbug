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


def _ctx(artifact=_DIFF_PAYLOAD):
    async def download(key):
        assert key == "changes/phabricator_diff.json"
        return json.dumps(artifact).encode()

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
