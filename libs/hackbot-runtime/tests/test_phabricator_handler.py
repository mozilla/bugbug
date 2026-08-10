"""Tests for the apply-side Phabricator action handler.

Mocks the Conduit API calls (`_conduit_request`) so these exercise the
handler's own logic — payload relay, create-vs-update transaction building,
result parsing — without a network call. The handler itself does no git/
subprocess work at all; that happens agent-side (see test_changes.py's
`build_phabricator_diff` tests).
"""

import json
from unittest.mock import AsyncMock

import pytest
from hackbot_runtime.actions.handlers import ApplyContext, phabricator_handler
from hackbot_runtime.actions.handlers.registry import get_handler
from hackbot_runtime.actions.phabricator import PATCH_ACTION_TYPES


@pytest.fixture(autouse=True)
def _phabricator_env(monkeypatch):
    """Provide a dummy Phabricator API key for the handler's client.

    The handler builds a PhabricatorClient, whose settings now require an API
    key (Conduit calls themselves are mocked, so the value is irrelevant beyond
    matching the required format). Reset the cached client and the resolved
    repository PHID (alru_cache) so each test starts clean.
    """
    monkeypatch.setenv("PHABRICATOR_API_KEY", "api-" + "a" * 28)
    phabricator_handler._client.cache_clear()
    phabricator_handler._repository_phid.cache_clear()
    yield
    phabricator_handler._client.cache_clear()


_DIFF_PAYLOAD = {
    "changes": [{"currentPath": "file.txt"}],
    "sourceControlBaseRevision": "abc123",
    "sourceControlPath": "/",
    "sourceControlSystem": "git",
    "branch": "HEAD",
}


# The agent-built artifact always carries both keys (see
# changes.build_phabricator_diff), so handlers may rely on them.
_LOCAL_COMMITS = {"node1": {"author": "Hackbot", "commit": "node1"}}


def _ctx(diff=_DIFF_PAYLOAD, local_commits=None):
    submission = {
        "diff": diff,
        "local_commits": local_commits or dict(_LOCAL_COMMITS),
    }

    async def download(key):
        assert key == "changes/phabricator_diff.json"
        return json.dumps(submission).encode()

    return ApplyContext(run_id="run-1", download_artifact=download)


def _fake_conduit(responses):
    calls = []

    async def fake(method, **payload):
        calls.append((method, payload))
        return responses.get(method, {})

    return fake, calls


def test_each_patch_action_type_has_its_own_handler():
    assert isinstance(
        get_handler("phabricator.submit_patch"), phabricator_handler.SubmitPatchHandler
    )
    assert isinstance(
        get_handler("phabricator.update_patch"), phabricator_handler.UpdatePatchHandler
    )
    # Every type the recording side can emit is registered.
    assert all(get_handler(t) is not None for t in PATCH_ACTION_TYPES)


def test_revision_title_strips_wip_prefix():
    rt = phabricator_handler._revision_title
    assert rt("Fix bug") == "Fix bug"
    assert rt("WIP: Fix bug") == "Fix bug"


async def test_submit_patch_creates_planned_changes_revision(monkeypatch):
    fake, calls = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-1", "diffid": 1},
            "differential.revision.edit": {"object": {"id": 555, "phid": "PHID-REV-1"}},
        }
    )
    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(
        phabricator_handler, "_repository_phid", AsyncMock(return_value="PHID-REPO-1")
    )

    result = await phabricator_handler.SubmitPatchHandler().apply(
        {"bug_id": 1, "title": "Fix", "summary": "s"},
        _ctx(),
    )

    assert result.status == "applied"
    assert result.result == {
        "revision_id": 555,
        "url": "https://phabricator.services.mozilla.com/D555",
    }

    creatediff_call = next(c for c in calls if c[0] == "differential.creatediff")
    assert creatediff_call[1]["repositoryPHID"] == "PHID-REPO-1"
    assert creatediff_call[1]["changes"] == _DIFF_PAYLOAD["changes"]

    edit_call = next(c for c in calls if c[0] == "differential.revision.edit")
    assert "objectIdentifier" not in edit_call[1]
    transactions = {t["type"]: t.get("value") for t in edit_call[1]["transactions"]}
    assert transactions["update"] == "PHID-DIFF-1"
    # Everything hackbot creates is a draft: the revision is marked
    # changes-planned, but the visible title does not carry a WIP prefix.
    assert transactions["title"] == "Fix"
    assert transactions["plan-changes"] is True
    assert "reviewers.add" not in transactions
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
    monkeypatch.setattr(
        phabricator_handler, "_repository_phid", AsyncMock(return_value="PHID-REPO-1")
    )

    # Only the git-derived fields exist in the artifact; summary + message are
    # filled in apply-side, mirroring moz-phab's set_diff_property.
    git_fields = {
        "author": "Hackbot",
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
    assert stored["author"] == "Hackbot"
    assert stored["tree"] == "tree1"
    assert stored["parents"] == ["base1"]
    # The stored title matches the visible revision title and reviewers are empty.
    assert stored["summary"] == "Fix the thing"
    assert stored["message"].startswith("Fix the thing\n\nSummary:\ndoes it")
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


async def test_update_patch_only_updates_the_diff(monkeypatch):
    fake, calls = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-2", "diffid": 2},
            "differential.revision.edit": {"object": {"id": 12345}},
        }
    )
    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(
        phabricator_handler, "_repository_phid", AsyncMock(return_value="PHID-REPO-1")
    )

    result = await phabricator_handler.UpdatePatchHandler().apply(
        {"revision_id": 12345}, _ctx()
    )

    # Nothing is reported back: an update creates no new revision, and its URL
    # was already known to whoever recorded the action.
    assert result.status == "applied"
    assert result.result is None

    # Exactly one edit, carrying nothing but the new diff: the revision's title,
    # summary, bug id and review status are all left alone.
    edits = [c for c in calls if c[0] == "differential.revision.edit"]
    assert len(edits) == 1
    assert edits[0][1]["objectIdentifier"] == 12345
    assert edits[0][1]["transactions"] == [{"type": "update", "value": "PHID-DIFF-2"}]
    # The revision is read, but only to rebuild the local:commits message.
    assert "differential.revision.search" in [c[0] for c in calls]


async def test_update_patch_local_commits_use_the_revisions_own_fields(monkeypatch):
    fake, calls = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-1", "diffid": 3},
            "differential.revision.edit": {"object": {"id": 42}},
            "differential.revision.search": {
                "data": [
                    {
                        "fields": {
                            "title": "WIP: Existing title",
                            "summary": "old sum",
                            "bugzilla.bug-id": "9",
                        }
                    }
                ]
            },
            "differential.setdiffproperty": {},
        }
    )
    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(
        phabricator_handler, "_repository_phid", AsyncMock(return_value="PHID-REPO-1")
    )

    result = await phabricator_handler.UpdatePatchHandler().apply(
        {"revision_id": 42},
        _ctx(local_commits={"n": {"author": "A"}}),
    )
    assert result.status == "applied"

    # The commit message mirrors the revision as it stands: its own title,
    # summary and bug id, verbatim.
    stored = json.loads(
        next(c for c in calls if c[0] == "differential.setdiffproperty")[1]["data"]
    )["n"]
    assert stored["summary"] == "WIP: Existing title"
    assert stored["message"].startswith("WIP: Existing title\n\nSummary:\nold sum")
    assert "Bug #: 9" in stored["message"]
    assert (
        "Differential Revision: https://phabricator.services.mozilla.com/D42"
        in stored["message"]
    )


async def test_update_patch_preserves_previous_diff_commit_author(monkeypatch):
    fake, calls = _fake_conduit(
        {
            "differential.creatediff": {"phid": "PHID-DIFF-NEW", "diffid": 8},
            "differential.revision.edit": {"object": {"id": 42}},
            "differential.revision.search": {
                "data": [
                    {
                        "fields": {
                            "title": "WIP: Existing title",
                            "summary": "old sum",
                            "bugzilla.bug-id": "9",
                            "diffID": 7,
                        }
                    }
                ]
            },
            "differential.diff.search": {
                "data": [
                    {
                        "id": 1335196,
                        "type": "DIFF",
                        "phid": "PHID-DIFF-j6xoinvjxogsqyfmelha",
                        "attachments": {
                            "commits": {
                                "commits": [
                                    {
                                        "identifier": "d0b3319556e92ee5f25590c40562f4ce4d7909f2",
                                        "author": {
                                            "name": "Patch Author",
                                            "email": "author@mozilla.example",
                                            "raw": "Patch Author <author@mozilla.example>",
                                            "epoch": 1,
                                        },
                                    }
                                ]
                            }
                        },
                    }
                ]
            },
            "differential.setdiffproperty": {},
        }
    )
    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(
        phabricator_handler, "_repository_phid", AsyncMock(return_value="PHID-REPO-1")
    )

    result = await phabricator_handler.UpdatePatchHandler().apply(
        {"revision_id": 42},
        _ctx(
            local_commits={
                "new-node": {
                    "author": "Hackbot",
                    "authorEmail": "hackbot@mozilla.tld",
                    "commit": "new-node",
                }
            }
        ),
    )
    assert result.status == "applied"

    prop_call = next(c for c in calls if c[0] == "differential.setdiffproperty")
    stored = json.loads(prop_call[1]["data"])["new-node"]
    assert stored["author"] == "Patch Author"
    assert stored["authorEmail"] == "author@mozilla.example"
    assert stored["commit"] == "new-node"


@pytest.mark.parametrize(
    ("handler", "params"),
    [
        ("SubmitPatchHandler", {"bug_id": 1, "title": "x"}),
        ("UpdatePatchHandler", {"revision_id": 7}),
    ],
)
async def test_missing_artifact_fails(handler, params):
    async def download(key):
        raise KeyError(key)

    ctx = ApplyContext(run_id="run-1", download_artifact=download)
    result = await getattr(phabricator_handler, handler)().apply(params, ctx)
    assert result.status == "failed"
    assert "No Phabricator submission artifact" in result.error


async def test_submit_patch_conduit_error_fails(monkeypatch):
    async def fake(method, **payload):
        raise RuntimeError("Conduit error ERR-CONDUIT-CORE: bad request")

    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    monkeypatch.setattr(
        phabricator_handler, "_repository_phid", AsyncMock(return_value="PHID-REPO-1")
    )

    result = await phabricator_handler.SubmitPatchHandler().apply(
        {"bug_id": 1, "title": "x"}, _ctx()
    )
    assert result.status == "failed"
    assert "ERR-CONDUIT-CORE" in result.error


async def test_add_comment_posts_comment_transaction(monkeypatch):
    fake, calls = _fake_conduit({"differential.revision.edit": {"object": {"id": 42}}})
    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)

    result = await phabricator_handler.AddCommentHandler().apply(
        {"revision_id": 42, "text": "Answering your question."},
        ApplyContext(run_id="run-1", download_artifact=None),
    )

    assert result.status == "applied"
    assert result.result == {
        "revision_id": 42,
        "revision_url": "https://phabricator.services.mozilla.com/D42",
    }
    edit_call = next(c for c in calls if c[0] == "differential.revision.edit")
    assert edit_call[1]["objectIdentifier"] == 42
    assert edit_call[1]["transactions"] == [
        {"type": "comment", "value": "Answering your question."}
    ]


async def test_add_comment_conduit_error_fails(monkeypatch):
    async def fake(method, **payload):
        raise RuntimeError("Conduit error ERR-CONDUIT-CORE: nope")

    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)

    result = await phabricator_handler.AddCommentHandler().apply(
        {"revision_id": 42, "text": "x"},
        ApplyContext(run_id="run-1", download_artifact=None),
    )
    assert result.status == "failed"
    assert "ERR-CONDUIT-CORE" in result.error


async def test_repository_phid_prefers_env_var(monkeypatch):
    monkeypatch.setenv("PHABRICATOR_REPOSITORY_PHID", "PHID-FROM-ENV")
    assert await phabricator_handler._repository_phid() == "PHID-FROM-ENV"


async def test_repository_phid_looks_up_by_short_name(monkeypatch):
    monkeypatch.delenv("PHABRICATOR_REPOSITORY_PHID", raising=False)

    async def fake(method, **payload):
        assert method == "diffusion.repository.search"
        return {
            "data": [
                {"phid": "PHID-OTHER", "fields": {"shortName": "other-repo"}},
                {"phid": "PHID-MC", "fields": {"shortName": "mozilla-central"}},
            ]
        }

    monkeypatch.setattr(phabricator_handler, "_conduit_request", fake)
    assert await phabricator_handler._repository_phid() == "PHID-MC"
