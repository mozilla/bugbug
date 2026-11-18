"""CI outlives the investigation execution, with one run and no new model calls."""

import base64
import json
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from app import gcs, jobs
from app import perf_regression as ci
from app.jobs import ExecutionStatus
from app.routers import events
from app.routers.runs import finalize_run
from app.schemas import ArtifactRef, RunSummary


class DB:
    def __init__(self, run):
        self.run = run
        self.commits = 0

    async def get(self, model, run_id, **kwargs):
        return self.run if run_id == self.run.run_id else None

    async def execute(self, statement):
        self.statement = statement
        return SimpleNamespace(
            scalar_one=lambda: self.run,
            scalars=lambda: SimpleNamespace(all=lambda: [self.run.run_id]),
        )

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.fixture
def run():
    return SimpleNamespace(
        run_id=uuid.uuid4(),
        agent="perf-regression",
        inputs={"bug_id": 1},
        results_prefix="runs/same-run/",
        status="running",
        finalized_at=None,
        execution_name="investigation",
        ci_wait=None,
        error=None,
        summary=None,
        artifacts=[],
        requested_by=None,
    )


@pytest.fixture
def services(monkeypatch):
    services = SimpleNamespace(
        status=AsyncMock(return_value=ExecutionStatus.succeeded),
        summary=AsyncMock(),
        artifacts=AsyncMock(
            return_value=[
                ArtifactRef(name="investigation/continuation.json", size=10),
                ArtifactRef(name="attempts/1/candidate.patch", size=10),
            ]
        ),
        ready=AsyncMock(return_value=False),
        launch=AsyncMock(return_value="validation-execution"),
        policy=AsyncMock(return_value={"url": "upload", "fields": {}}),
        download=AsyncMock(return_value="https://storage.googleapis.com/signed"),
        promote=AsyncMock(side_effect=lambda _run_id, _prefix, summary: summary),
        publish=AsyncMock(),
        push=AsyncMock(return_value=PUSHED),
    )
    monkeypatch.setattr(jobs, "get_execution_status", services.status)
    monkeypatch.setattr(jobs, "trigger_execution", services.launch)
    monkeypatch.setattr(gcs, "read_summary", services.summary)
    monkeypatch.setattr(gcs, "list_artifacts", services.artifacts)
    monkeypatch.setattr(gcs, "generate_results_policy", services.policy)
    monkeypatch.setattr(gcs, "generate_artifact_download_url", services.download)
    monkeypatch.setattr(gcs, "promote_artifacts", services.promote)
    monkeypatch.setattr(ci, "ready", services.ready)
    monkeypatch.setattr(ci, "_try_push", services.push)
    monkeypatch.setattr(
        "app.routers.runs.pubsub.publish_run_completed", services.publish
    )
    monkeypatch.setattr(
        "app.perf_regression.pubsub.publish_run_completed", services.publish
    )
    return services


# The Lando job the API's own try-server handler created.
PUSHED = {
    "job_id": 42,
    "status_url": "https://lando.moz.tools/landing_jobs/42/",
    "try_url": "https://treeherder.mozilla.org/jobs?repo=try&landoCommitID=42",
}


def waiting(**extra):
    return RunSummary(
        status="ok",
        findings={
            "status": "awaiting_ci",
            "result": "Investigation and candidate saved",
            "ci_pending": {"deadline": time.time() + 86400},
            **extra,
        },
        actions=[
            {
                "type": "try_server.push",
                "params": {"tasks": ["test-perf"], "rebuild": 5},
            }
        ],
    )


async def test_original_execution_exits_without_finalizing_run(run, services):
    services.summary.return_value = waiting()
    db = DB(run)
    await finalize_run(db, run)
    assert run.status == "awaiting_ci"
    assert run.execution_name is None
    assert run.finalized_at is None
    assert run.summary["findings"]["result"] == "Investigation and candidate saved"
    assert len(run.artifacts) == 2
    services.launch.assert_not_awaited()
    services.publish.assert_not_awaited()


async def test_pulse_resumes_same_run_once_with_fresh_grants(run, services):
    services.summary.return_value = waiting()
    db = DB(run)
    await finalize_run(db, run)
    run.ci_wait.update(next_check=0, last_check=0, task_group_id="group")
    services.ready.return_value = True
    request = SimpleNamespace(
        json=AsyncMock(
            return_value={"message": {"attributes": {"task_group_id": "group"}}}
        )
    )
    await events.try_task_finished(request, db)
    await ci.resume(db, run)  # Duplicate delivery / overlapping sweep.
    services.launch.assert_awaited_once()
    _, env = services.launch.call_args.args
    assert env["RUN_ID"] == str(run.run_id)
    prefix = f"_validation/{env['PERF_VALIDATION_ID']}/"
    assert run.ci_wait["artifact_prefix"] == prefix
    assert env["RESULTS_PREFIX"] == run.results_prefix + prefix
    assert env["PERF_VALIDATION_ID"] == run.ci_wait["validation_id"]
    assert env["PERF_VALIDATION_STATE_URL"].startswith("https://storage")
    assert services.launch.call_args.kwargs == {
        "timeout_seconds": ci.VALIDATION_SECONDS
    }
    services.policy.assert_awaited_once_with(
        str(run.run_id),
        timeout_seconds=ci.VALIDATION_SECONDS,
        artifact_prefix=prefix,
    )
    assert run.status == "running"
    assert run.execution_name == "validation-execution"
    assert run.finalized_at is None


async def test_validation_with_indexing_lag_releases_execution_again(run, services):
    run.ci_wait = {
        "validation_id": "first",
        "task_group_id": "group",
        "artifact_prefix": "_validation/first/",
    }
    services.summary.return_value = waiting(validation_id="first")
    # Keep the exact deadline, not a fresh 24h on every retry.
    services.ready.return_value = True
    await finalize_run(DB(run), run)
    assert run.status == "awaiting_ci"
    assert run.ci_wait["task_group_id"] == "group"
    assert run.ci_wait["relaunches"] == 1
    # Backed off: not relaunched straight away even though CI looks done.
    assert run.ci_wait["not_before"] > time.time() + 200
    services.launch.assert_not_awaited()
    services.publish.assert_not_awaited()
    services.promote.assert_awaited_once()


async def test_validator_finalizes_original_run_with_verdict(run, services):
    run.ci_wait = {"validation_id": "v", "artifact_prefix": "_validation/v/"}
    services.summary.return_value = RunSummary(
        status="ok", findings={"validation_id": "v", "status": "fixed"}
    )
    await finalize_run(DB(run), run)
    assert run.status == "succeeded"
    assert run.finalized_at is not None
    assert run.summary["findings"]["status"] == "fixed"
    services.summary.assert_awaited_once_with(
        str(run.run_id), artifact_prefix="_validation/v/"
    )
    services.promote.assert_awaited_once_with(
        str(run.run_id), "_validation/v/", services.summary.return_value
    )
    services.publish.assert_awaited_once()


async def test_stale_summary_is_retried_not_trusted(run, services):
    run.ci_wait = {
        "validation_id": "new",
        "artifact_prefix": "_validation/new/",
        "deadline": time.time() + 3600,
    }
    services.summary.return_value = waiting(validation_id="old")
    await finalize_run(DB(run), run)
    assert run.status == "awaiting_ci"
    assert run.ci_wait["validation_failures"] == 1
    assert "own summary" in run.ci_wait["last_validation_error"]
    services.launch.assert_not_awaited()
    services.promote.assert_not_awaited()


@pytest.mark.parametrize(
    "exec_status", [ExecutionStatus.failed, ExecutionStatus.cancelled]
)
async def test_crashed_validator_is_retried_then_fails(
    run, services, monkeypatch, exec_status
):
    run.ci_wait = {"validation_id": "v", "deadline": time.time() + 3600}
    run.execution_name = "validation-execution"
    run.requested_by = "author@mozilla.com"
    services.status.return_value = exec_status
    services.summary.return_value = None
    notify = AsyncMock()
    monkeypatch.setattr("app.notifications.notify_requester", notify)
    for attempt in range(1, ci.MAX_VALIDATION_FAILURES):
        await finalize_run(DB(run), run)
        assert run.status == "awaiting_ci"
        assert run.ci_wait["validation_failures"] == attempt
        assert run.ci_wait["not_before"] > time.time()
        run.status, run.execution_name = "running", "validation-execution"
        run.ci_wait["validation_id"] = "v"
    await finalize_run(DB(run), run)
    assert run.status == "failed"
    assert run.finalized_at is not None
    services.publish.assert_awaited_once()
    notify.assert_awaited_once()


async def test_expired_launch_claim_goes_back_to_waiting(run, services):
    run.status = "running"
    run.execution_name = None
    run.ci_wait = {
        "validation_id": "v",
        "launching_until": time.time() - 1,
        "deadline": time.time() + 3600,
    }
    await finalize_run(DB(run), run)
    assert run.status == "awaiting_ci"
    assert run.ci_wait["launch_failures"] == 1
    assert run.finalized_at is None
    services.status.assert_not_awaited()


async def test_late_launch_does_not_reclaim_a_released_run(run, services):
    run.status = "awaiting_ci"
    run.ci_wait = {"job_id": 42, "deadline": time.time() - 1}

    async def slow_launch(*args, **kwargs):
        # Meanwhile the sweep released the expired claim.
        run.status = "awaiting_ci"
        run.ci_wait = {**run.ci_wait, "validation_id": None}
        return "late-execution"

    services.launch.side_effect = slow_launch
    await ci.resume(DB(run), run)
    assert run.execution_name is None


async def test_deadline_starts_one_final_snapshot_even_when_ci_never_finishes(
    run, services
):
    run.status = "awaiting_ci"
    run.ci_wait = {"job_id": 42, "deadline": time.time() - 1}
    await ci.resume(DB(run), run)
    services.ready.assert_not_awaited()
    services.launch.assert_awaited_once()


async def test_finalized_and_throttled_runs_do_not_resume(run, services):
    run.status = "awaiting_ci"
    run.ci_wait = {"next_check": time.time() + 60}
    await ci.resume(DB(run), run)
    run.finalized_at = datetime.now(timezone.utc)
    await ci.resume(DB(run), run)
    services.ready.assert_not_awaited()
    services.launch.assert_not_awaited()


@pytest.mark.parametrize(
    "pending",
    [{"deadline": "soon"}, {"deadline": time.time() + 90000}, {"deadline": 0}],
)
def test_reject_invalid_handoff(pending):
    summary = waiting()
    summary.findings["ci_pending"].update(pending)
    with pytest.raises(ValueError, match="Invalid"):
        ci.pending_watch(summary)


@pytest.mark.parametrize(
    "state,expected",
    [("running", False), ("completed", True), ("failed", True), ("exception", True)],
)
async def test_ready_resolves_receipt_and_paginates_group(state, expected):
    def task(task_id, task_state, deps=()):
        return {
            "status": {"taskId": task_id, "state": task_state},
            "task": {"dependencies": list(deps)},
        }

    def handle(request):
        path = request.url.path
        if "landing_jobs" in path:
            data = {"commit_id": "a" * 40}
        elif path.endswith("/push/"):
            data = {"results": [{"id": 1, "revision": "a" * 40}]}
        elif path.endswith("/jobs/"):
            data = {"results": [{"task_id": "task"}]}
        elif path.endswith("/task/task"):
            data = {"taskGroupId": "group"}
        elif request.url.params.get("continuationToken"):
            data = {"tasks": [task("b", state)]}
        else:
            data = {"tasks": [task("a", "completed")], "continuationToken": "second"}
        return httpx.Response(200, json=data)

    watch = {"status_url": "https://lando.moz.tools/landing_jobs/42/"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        assert await ci.ready(watch, http) is expected
    assert watch["task_group_id"] == "group"
    assert watch["revision"] == "a" * 40


def _task(task_id, state, deps=()):
    return {
        "status": {"taskId": task_id, "state": state},
        "task": {"dependencies": list(deps)},
    }


@pytest.mark.parametrize(
    "tasks,expected",
    [
        # Tests behind a failed build never get scheduled; that is settled.
        ([_task("b", "failed"), _task("t", "unscheduled", ["b"])], True),
        (
            [
                _task("b", "exception"),
                _task("s", "unscheduled", ["b"]),
                _task("t", "unscheduled", ["s"]),
            ],
            True,
        ),
        # Waiting on a dependency that has not failed is not settled.
        ([_task("b", "completed"), _task("t", "unscheduled", ["b"])], False),
    ],
)
def test_blocked_tasks_settle_the_group(tasks, expected):
    assert ci._settled(tasks) is expected


async def test_launch_failure_is_retried_with_backoff_then_fails(run, services):
    run.status = "awaiting_ci"
    run.ci_wait = {"job_id": 42, "deadline": time.time() - 1}
    services.launch.side_effect = TimeoutError("Launch response lost")
    db = DB(run)
    await ci.resume(db, run)
    assert run.status == "awaiting_ci"
    assert run.execution_name is None
    assert run.ci_wait["launch_failures"] == 1
    assert run.ci_wait["not_before"] > time.time()
    # Backoff: an immediate replay does not launch again.
    await ci.resume(db, run)
    services.launch.assert_awaited_once()
    for _ in range(ci.MAX_LAUNCH_FAILURES - 1):
        run.ci_wait["not_before"] = 0
        await ci.resume(db, run)
    assert services.launch.await_count == ci.MAX_LAUNCH_FAILURES
    assert run.status == "failed"
    assert run.finalized_at is not None
    assert "Could not launch" in run.error
    services.publish.assert_awaited_once()


async def test_missing_saved_state_backs_off_instead_of_retrying_forever(run, services):
    run.status = "awaiting_ci"
    run.ci_wait = {"job_id": 42, "deadline": time.time() - 1}
    services.download.return_value = None
    await ci.resume(DB(run), run)
    assert run.status == "awaiting_ci"
    assert run.ci_wait["launch_failures"] == 1
    assert "Missing saved" in run.ci_wait["last_launch_error"]
    services.launch.assert_not_awaited()


async def test_run_fails_when_ci_never_validates_long_after_deadline(run, services):
    run.status = "awaiting_ci"
    run.ci_wait = {
        "job_id": 42,
        "deadline": time.time() - ci.DEADLINE_GRACE_SECONDS - 60,
    }
    services.download.return_value = None
    await ci.resume(DB(run), run)
    assert run.status == "failed"
    services.publish.assert_awaited_once()


async def test_invalid_continuation_fails_the_run_instead_of_raising(run, services):
    summary = waiting()
    summary.findings["ci_pending"]["deadline"] = "never"
    services.summary.return_value = summary
    await finalize_run(DB(run), run)
    assert run.status == "failed"
    assert "Invalid" in run.error
    services.launch.assert_not_awaited()


@pytest.mark.parametrize(
    "body", [{}, {"message": None}, {"message": {"attributes": None}}, []]
)
async def test_malformed_pulse_push_is_ignored(run, body):
    request = SimpleNamespace(json=AsyncMock(return_value=body))
    await events.try_task_finished(request, DB(run))


async def test_reconciliation_recovers_early_or_lost_pulse_event(run, services):
    services.summary.return_value = waiting()
    db = DB(run)
    await finalize_run(db, run)
    run.ci_wait["next_check"] = 0
    services.ready.return_value = True
    # The maintenance endpoint invokes the same finalizer, without any Pulse event.
    await finalize_run(db, run)
    assert run.execution_name == "validation-execution"
    services.launch.assert_awaited_once()


@pytest.fixture
def memory_gcs(monkeypatch):
    """Exercise the actual GCS helpers against a bucket kept in memory."""
    objects = {}
    client, bucket = MagicMock(), MagicMock()

    def put(name, data, content_type="text/plain"):
        objects[name] = {
            "data": data.encode() if isinstance(data, str) else data,
            "content_type": content_type,
            "generation": objects.get(name, {}).get("generation", 0) + 1,
        }

    def blob(name):
        obj = objects.get(name, {})
        item = MagicMock()
        item.name = name
        item.size = len(obj.get("data", b""))
        item.content_type = obj.get("content_type")
        item.generation = obj.get("generation", 0)
        item.exists.side_effect = lambda: name in objects
        item.download_as_bytes.side_effect = lambda: objects[name]["data"]
        item.upload_from_string.side_effect = lambda data, content_type: put(
            name, data, content_type
        )
        return item

    def copy(source, _bucket, *, new_name, if_source_generation_match):
        obj = objects[source.name]
        assert obj["generation"] == if_source_generation_match
        put(new_name, obj["data"], obj["content_type"])

    client.bucket.return_value = bucket
    bucket.blob.side_effect = blob
    bucket.copy_blob.side_effect = copy
    client.list_blobs.side_effect = lambda _bucket, prefix: [
        blob(name) for name in list(objects) if name.startswith(prefix)
    ]
    monkeypatch.setattr(gcs, "_client", lambda: client)
    monkeypatch.setattr(ci.settings, "results_bucket", "test")
    return SimpleNamespace(objects=objects, put=put, bucket=bucket)


@pytest.mark.parametrize("outcome", ["fixed", "awaiting_ci"])
async def test_lost_launch_response_cannot_overwrite_accepted_outputs(
    run, services, memory_gcs, monkeypatch, outcome
):
    """An accepted-but-unregistered worker can only change its private files."""
    root = gcs.run_prefix(str(run.run_id))
    run.results_prefix = root
    original = waiting()
    memory_gcs.put(root + "summary.json", original.model_dump_json())
    memory_gcs.put(root + "investigation/continuation.json", '{"initial":true}')
    memory_gcs.put(root + "attempts/1/candidate.patch", b"original candidate")
    run.status = "awaiting_ci"
    run.ci_wait = dict(original.findings["ci_pending"])
    services.ready.return_value = True
    accepted = []

    async def launch(_job, env, **_kwargs):
        accepted.append(env)
        if len(accepted) == 1:
            raise TimeoutError("Server accepted the execution; response was lost")
        return "tracked-execution"

    creds = MagicMock(service_account_email="test@example.com")
    creds.sign_bytes.return_value = b"signature"
    monkeypatch.setattr(gcs, "_signing_credentials", lambda: creds)

    async def policy(run_id, timeout_seconds, *, artifact_prefix):
        return gcs._generate_post_policy_sync(
            run_id, timeout_seconds, artifact_prefix=artifact_prefix
        )

    async def read_summary(run_id, *, artifact_prefix=""):
        return gcs._read_summary_sync(run_id, artifact_prefix)

    async def promote(run_id, prefix, summary):
        return gcs._promote_artifacts_sync(run_id, prefix, summary)

    async def artifacts(run_id):
        return gcs._list_artifacts_sync(run_id)

    monkeypatch.setattr(gcs, "generate_results_policy", policy)
    monkeypatch.setattr(gcs, "read_summary", read_summary)
    monkeypatch.setattr(gcs, "promote_artifacts", promote)
    monkeypatch.setattr(gcs, "list_artifacts", artifacts)
    services.launch.side_effect = launch
    db = DB(run)
    await ci.resume(db, run)
    run.ci_wait["not_before"] = 0
    await ci.resume(db, run)
    orphan, tracked = accepted
    assert orphan["RESULTS_PREFIX"] != tracked["RESULTS_PREFIX"]
    assert run.execution_name == "tracked-execution"

    def upload(env, key, data):
        fields = json.loads(env["RESULTS_POLICY_FIELDS"])
        conditions = json.loads(base64.b64decode(fields["policy"]))["conditions"]
        allowed = next(
            c[2]
            for c in conditions
            if isinstance(c, list) and c[:2] == ["starts-with", "$key"]
        )
        if not key.startswith(allowed):
            raise PermissionError("Object key is outside this execution's grant")
        memory_gcs.put(key, data)

    with pytest.raises(PermissionError):
        upload(orphan, root + "summary.json", "must be rejected")
    with pytest.raises(PermissionError):
        upload(orphan, tracked["RESULTS_PREFIX"] + "summary.json", "must be rejected")

    summary = RunSummary(
        status="ok",
        findings={
            "status": outcome,
            "validation_id": tracked["PERF_VALIDATION_ID"],
            **(
                {
                    "verified_patch_url": f"gs://test/{tracked['RESULTS_PREFIX']}verified/fix.patch"
                }
                if outcome == "fixed"
                else {}
            ),
            **(
                {"ci_pending": original.findings["ci_pending"]}
                if outcome == "awaiting_ci"
                else {}
            ),
        },
    )
    upload(
        tracked, tracked["RESULTS_PREFIX"] + "summary.json", summary.model_dump_json()
    )
    upload(
        tracked,
        tracked["RESULTS_PREFIX"] + "investigation/report.md",
        "accepted report",
    )
    if outcome == "fixed":
        upload(
            tracked, tracked["RESULTS_PREFIX"] + "verified/fix.patch", "accepted patch"
        )
    if outcome == "awaiting_ci":
        upload(
            tracked,
            tracked["RESULTS_PREFIX"] + "investigation/continuation.json",
            '{"ci_state":"accepted"}',
        )

    await finalize_run(db, run)
    assert run.status == ("succeeded" if outcome == "fixed" else "awaiting_ci")
    assert (
        memory_gcs.objects[root + "investigation/report.md"]["data"]
        == b"accepted report"
    )
    if outcome == "fixed":
        assert (
            run.summary["findings"]["verified_patch_url"]
            == f"gs://test/{root}verified/fix.patch"
        )
    else:
        assert root + "verified/fix.patch" not in memory_gcs.objects
    canonical = {
        k: dict(v)
        for k, v in memory_gcs.objects.items()
        if not k.startswith(root + "_validation/")
    }
    assert run.artifacts and all(
        not a["name"].startswith("_validation/") for a in run.artifacts
    )
    if outcome == "awaiting_ci":
        assert (
            memory_gcs.objects[root + "investigation/continuation.json"]["data"]
            == b'{"ci_state":"accepted"}'
        )
    else:
        services.publish.assert_awaited_once()

    # The orphan finishes after the tracked execution, with a different verdict.
    upload(
        orphan,
        orphan["RESULTS_PREFIX"] + "summary.json",
        waiting(validation_id=orphan["PERF_VALIDATION_ID"]).model_dump_json(),
    )
    upload(
        orphan,
        orphan["RESULTS_PREFIX"] + "investigation/report.md",
        "late orphan report",
    )
    upload(orphan, orphan["RESULTS_PREFIX"] + "verified/fix.patch", "late orphan patch")
    assert {
        k: v
        for k, v in memory_gcs.objects.items()
        if not k.startswith(root + "_validation/")
    } == canonical


def test_artifact_promotion_is_retryable_and_writes_summary_last(memory_gcs):
    root = gcs.run_prefix("run")
    prefix = "_validation/attempt/"
    memory_gcs.put(root + "summary.json", '{"status":"ok","findings":{"old":true}}')
    memory_gcs.put(root + prefix + "investigation/report.md", "new report")
    memory_gcs.put(root + prefix + "verified/fix.patch", "new patch")
    summary = RunSummary(status="ok", findings={"validation_id": "attempt"})
    copy = memory_gcs.bucket.copy_blob.side_effect

    def fail_on_patch(source, *args, **kwargs):
        if source.name.endswith("fix.patch"):
            raise TimeoutError("Transient copy error")
        return copy(source, *args, **kwargs)

    memory_gcs.bucket.copy_blob.side_effect = fail_on_patch
    with pytest.raises(TimeoutError):
        gcs._promote_artifacts_sync("run", prefix, summary)
    assert json.loads(memory_gcs.objects[root + "summary.json"]["data"])[
        "findings"
    ] == {"old": True}
    memory_gcs.bucket.copy_blob.side_effect = copy
    published = gcs._promote_artifacts_sync("run", prefix, summary)
    assert published == gcs._read_summary_sync("run")
    assert memory_gcs.objects[root + "verified/fix.patch"]["data"] == b"new patch"


async def test_validation_gets_the_lando_job_of_the_applied_push(run, services):
    services.summary.return_value = waiting()
    db = DB(run)
    await finalize_run(db, run)
    services.push.assert_awaited()
    assert run.ci_wait["status_url"] == PUSHED["status_url"]
    run.ci_wait.update(next_check=0, last_check=0)
    services.ready.return_value = True
    await ci.resume(db, run)
    _, env = services.launch.call_args.args
    assert env["PERF_VALIDATION_STATUS_URL"] == PUSHED["status_url"]
    assert env["PERF_VALIDATION_TRY_URL"] == PUSHED["try_url"]


async def test_a_push_held_for_review_waits_then_expires(run, services):
    services.push.return_value = None
    run.status = "awaiting_ci"
    run.ci_wait = {"deadline": time.time() + 3600}
    await ci.resume(DB(run), run)
    assert run.status == "awaiting_ci"
    services.ready.assert_not_awaited()
    run.ci_wait["deadline"] = time.time() - 1
    await ci.resume(DB(run), run)
    assert run.status == "failed"
    assert "not applied" in run.error
    services.launch.assert_not_awaited()


async def test_a_failed_push_fails_the_run(run, services):
    services.push.side_effect = ci.TryPushFailed(
        "The try push failed: Repo try does not exist."
    )
    run.status = "awaiting_ci"
    run.ci_wait = {"deadline": time.time() + 3600}
    await ci.resume(DB(run), run)
    assert run.status == "failed"
    assert "Repo try does not exist" in run.error


async def test_the_recorded_push_is_applied_through_the_action_handlers(
    run, monkeypatch
):
    from app import actions_applier
    from app.action_handlers import try_server_handler

    row = SimpleNamespace(
        type="try_server.push", status="pending", result=None, error=None
    )
    monkeypatch.setattr(
        actions_applier, "ensure_action_rows", AsyncMock(return_value=[(row, [])])
    )

    async def apply(db, applied_run, rows, client):
        assert applied_run is run and rows == [(row, [])]
        row.status, row.result = "applied", {"job_id": 7, "url": "https://th/7"}

    monkeypatch.setattr(actions_applier, "apply_rows", apply)
    monkeypatch.setattr(actions_applier, "should_auto_apply", lambda r, t: True)
    monkeypatch.setattr(
        try_server_handler, "_client", lambda: SimpleNamespace(base_url="https://lando")
    )

    class RefreshingDB(DB):
        async def refresh(self, obj):
            pass

    assert await ci._try_push(RefreshingDB(run), run) == {
        "job_id": 7,
        "status_url": "https://lando/landing_jobs/7/",
        "try_url": "https://th/7",
    }
    # Held when the run requires review: nothing is applied.
    row.status = "pending"
    monkeypatch.setattr(actions_applier, "should_auto_apply", lambda r, t: False)
    assert await ci._try_push(RefreshingDB(run), run) is None
