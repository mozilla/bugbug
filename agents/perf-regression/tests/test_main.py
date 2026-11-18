# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Exercise real candidate worktrees and patch publication with fake model/CI."""

import json
from base64 import b64decode
from types import SimpleNamespace

import pytest
from hackbot_agents.perf_regression import __main__ as entry
from hackbot_agents.perf_regression.evidence import EvidenceError, metric_key
from hackbot_runtime import HackbotConfig, HackbotContext
from hackbot_runtime import context as runtime_context
from hackbot_runtime.runtime import _finish


@pytest.mark.parametrize(
    "outcome",
    [
        "fixed",
        "harness_fixed",
        "exhausted",
        "no_solution",
        "protected",
        "preflight",
        "ci_error",
        "ci_failure",
        "inconclusive",
        "deferred_fixed",
        "deferred_pending",
        "deferred_timeout",
    ],
)
async def test_only_verified_candidate_reaches_source_and_verified_artifacts(
    tmp_path, monkeypatch, row, outcome
):
    repo = tmp_path / "repo"
    repo.mkdir()
    entry.git(repo, "init")
    fixed = outcome in {"fixed", "harness_fixed", "deferred_fixed"}
    deferred = outcome.startswith("deferred_")
    source = (
        "testing/raptor/browsertime/speedometer3.js"
        if outcome == "harness_fixed"
        else "layout.cpp"
    )
    (repo / source).parent.mkdir(parents=True, exist_ok=True)
    (repo / source).write_text("baseline\n")
    entry.changes.commit_all(repo, "baseline")
    base = entry.git(repo, "rev-parse", "HEAD")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("RUN_ID", "run")
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.delenv("RESULTS_POLICY_URL", raising=False)
    monkeypatch.delenv("SOURCE_REPO", raising=False)
    ctx = HackbotContext.from_config_obj(
        HackbotConfig(
            source={
                "repo_url": "https://example.com/firefox.git",
                "checkout_path": repo,
            }
        )
    )
    artifacts = ctx.run_artifacts_dir
    manifest = {
        "bug_id": 1,
        "alert": {
            "id": 2,
            "repository": "autoland",
            "prev_push_revision": "good",
            "revision": "b" * 40,
            "push_id": 2,
        },
        "alert_id": 2,
        "metric_row_ids": [row["id"]],
        "bad_git": base,
        "good_git": "a" * 40,
        "metrics": [row],
        "tasks": ["test-speedometer3"],
        "harness_changes_allowed": outcome == "harness_fixed",
    }
    monkeypatch.setattr(entry.tempfile, "mkdtemp", lambda **_: str(scratch))
    # Deployed (Cloud Run) investigations hand CI to the API; local ones wait.
    if deferred:
        monkeypatch.setenv("CLOUD_RUN_JOB", "hackbot-agent-perf-regression")
    else:
        monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    monkeypatch.setattr(
        entry,
        "Inputs",
        lambda: SimpleNamespace(
            bug_id=1,
            alert_id=2,
            max_attempts=1,
            model=None,
            max_turns=10,
            effort="high",
            ci_timeout_seconds=60,
            perf_validation_state_url=None,
            suites=None,
        ),
    )

    def prepare(path, repo_url, ref, depth):
        assert path == repo
        assert ref == base
        assert depth == 100

    monkeypatch.setattr(runtime_context, "ensure_source_repo", prepare)

    async def collect(self, bug_id, alert_id, directory, suites=None):
        directory.mkdir()
        (directory / "bug.json").write_text('{"bug": {"id": 1}}')
        (directory / "raw-profile.json").write_text('{"threads": []}')
        return manifest

    proposed = []

    async def propose(tree, evidence, previous, **kwargs):
        number = len(proposed) + 1
        proposed.append(number)
        assert (tree / source).read_text() == "baseline\n"
        target = tree / ("try_task_config.json" if outcome == "protected" else source)
        target.write_text(f"candidate {number}\n")
        kwargs["log"].write_text(f"Candidate {number} analysis\n")
        kwargs["usage"].update(num_turns=1, total_cost_usd=0.1)
        return {
            "has_candidate": outcome != "no_solution",
            "summary": f"Candidate {number}",
            "regression_location": "layout.cpp: MeasureGrid on the main thread",
            "root_cause": "Each layout invalidates and repeats the grid measurements.",
            "profile_evidence": "Repeated MeasureGrid markers; no network-thread recording.",
            "correctness_checks": ["Targeted layout check passed; full suite not run."],
            "next_steps": "Cache the grid measurements across layouts.",
            "num_turns": 1,
            "total_cost_usd": 0.1,
        }

    def preflight(tree, base, log):
        log.write_text("Local lint/format check\n")
        if outcome == "preflight":
            raise EvidenceError("Local lint/format check failed (no try push)")

    monkeypatch.setattr(entry, "check_candidate", preflight)
    submissions = []
    waits = []
    collect_changes = entry.changes.collect

    def collect_candidate(tree, base, repo_url, **kwargs):
        assert repo_url == ctx.config.source.repo_url
        return collect_changes(tree, base, repo_url, **kwargs)

    monkeypatch.setattr(entry.changes, "collect", collect_candidate)

    async def push_to_try(checkout, push_base, tasks):
        # Local runs push the main checkout, which now holds the candidate.
        assert checkout == repo and push_base == base
        assert tasks == manifest["tasks"]
        assert entry.git(repo, "show", "HEAD:" + source) == "candidate 1"
        submissions.append("candidate-1")
        return {
            "job_id": 123,
            "status_url": "https://lando.moz.tools/landing_jobs/123/",
            "url": "https://treeherder.mozilla.org/jobs?repo=try&revision=candidate",
            "tasks": tasks,
            "repetitions": 5,
        }

    monkeypatch.setattr(entry, "push_to_try", push_to_try)

    class CI:
        def __init__(self, *args, **kwargs):
            pass

        async def wait(self, submission, progress):
            waits.append(submission)
            if outcome == "ci_error":
                raise EvidenceError("CI timed out")
            value = 100 if fixed else 102
            state = {
                "revision": "c" * 40,
                "url": "https://treeherder.mozilla.org/jobs?repo=try&revision=candidate",
                "complete": outcome not in {"deferred_pending", "deferred_timeout"},
                "measurements": {
                    metric_key(row["series_signature"]): [
                        {"job_id": i, "value": value + i * 0.0001} for i in range(5)
                    ]
                },
            }
            if outcome == "ci_failure":
                state["failure_reason"] = "CI build/test failure"
            progress(state)
            return state

        async def check(self, submission, previous):
            # Validation re-derives the tasks; it never trusts saved ones.
            assert submission["tasks"] == manifest["tasks"]
            assert submission["repetitions"] == 5
            return await self.wait(submission, lambda _: None)

        async def pushed_candidate(self, revision):
            pushed.append(revision)
            return "https://hg.mozilla.org/try/rev/" + "d" * 40

    pushed = []

    async def historical_baselines(self, alert, rows):
        def points(value, count):
            return [{"job_id": i, "value": value + i * 0.0001} for i in range(count)]

        key = metric_key(row["series_signature"])
        return {
            "repository": "autoland",
            "good": {key: points(100, 1 if outcome == "inconclusive" else 5)},
            "bad": {key: points(102, 5)},
            "pushes": {},
        }

    monkeypatch.setattr(entry.EvidenceClient, "baselines", historical_baselines)
    monkeypatch.setattr(entry.EvidenceClient, "collect", collect)
    monkeypatch.setattr(entry, "propose", propose)
    monkeypatch.setattr(entry, "TryClient", CI)
    if outcome in {"protected", "preflight", "ci_error"}:
        with pytest.raises(EvidenceError) as caught:
            await entry.main(ctx)
        assert _finish(ctx, caught.value) == 1
        saved_progress = json.loads(
            (artifacts / "investigation/progress.json").read_text()
        )
        assert saved_progress["status"] == "blocked"
        if outcome == "ci_error":
            assert saved_progress["positive_attempts"] == []
            assert len(saved_progress["attempts"]) == 1
            assert (artifacts / "attempts/1/candidate.patch").exists()
        if outcome == "preflight":
            assert (artifacts / "attempts/1/preflight.log").exists()
    else:
        result = await entry.main(ctx)
        if deferred:
            assert waits == []
            assert submissions == []
            assert result.status == "awaiting_ci"
            # hackbot-api applies the push and fills in the Lando job.
            assert set(result.ci_pending) == {"deadline"}
            assert not (artifacts / "verified/fix.patch").exists()
            assert len(proposed) == 1
            assert _finish(ctx, result) == 0
            investigation = json.loads((artifacts / "summary.json").read_text())
            [push] = investigation["actions"]
            assert push["type"] == "try_server.push"
            assert push["params"]["tasks"] == manifest["tasks"]
            assert push["params"]["rebuild"] == 5
            # The runtime publishes what the API's push handler sends to Lando.
            assert (artifacts / "changes/try_push.json").exists()
            submissions.append("recorded")
            # A validation execution starts from a fresh checkout.
            entry.git(repo, "reset", "-q", "--hard", base)
            state_path = artifacts / "investigation/continuation.json"
            saved = json.loads(state_path.read_text())
            if outcome == "deferred_timeout":
                saved["ci_pending"]["deadline"] = 0
                state_path.write_text(json.dumps(saved))
            monkeypatch.setattr(
                entry,
                "Inputs",
                lambda: SimpleNamespace(
                    bug_id=1,
                    alert_id=None,
                    perf_validation_state_url=str(state_path),
                    perf_validation_patch_url=str(
                        artifacts / "attempts/1/candidate.patch"
                    ),
                    perf_validation_id="validation-one",
                    perf_validation_status_url="https://lando.moz.tools/landing_jobs/123/",
                    perf_validation_try_url=(
                        "https://treeherder.mozilla.org/jobs?repo=try&revision=candidate"
                    ),
                ),
            )
            ctx = HackbotContext.from_config_obj(ctx.config)
            result = await entry.main(ctx)
            assert result.validation_id == "validation-one"
            assert len(proposed) == 1
            assert submissions == ["recorded"]
            if outcome != "deferred_pending":
                # The report links the candidate exactly as pushed to Try.
                assert pushed == ["c" * 40]
                report_text = (artifacts / "investigation/report.md").read_text()
                assert "try/rev/" + "d" * 40 in report_text
        assert result.status == (
            "fixed"
            if fixed
            else "exhausted"
            if outcome in {"ci_failure", "inconclusive", "deferred_timeout"}
            else "awaiting_ci"
            if outcome == "deferred_pending"
            else outcome
        )
        assert _finish(ctx, result) == 0
    summary = json.loads((artifacts / "summary.json").read_text())
    assert summary["status"] == (
        "error" if outcome in {"protected", "preflight", "ci_error"} else "ok"
    )
    report = (artifacts / "investigation/report.md").read_text()
    assert "layout.cpp: MeasureGrid on the main thread" in report
    assert "Each layout invalidates and repeats the grid measurements." in report
    assert "no network-thread recording" in report
    assert "full suite not run" in report
    # Next steps help the author only when the candidate did not settle it.
    assert ("Cache the grid measurements" in report) == (
        not fixed and outcome != "deferred_pending"
    )
    analysis = json.loads(
        (artifacts / "investigation/candidate-1-analysis.json").read_text()
    )
    assert analysis["root_cause"] in report
    assert (artifacts / "investigation/evidence/bug.json").exists()
    assert not (artifacts / "investigation/evidence/raw-profile.json").exists()
    if summary["status"] == "ok":
        assert summary["findings"]["result"] == report
    else:
        assert saved_progress["result"] == report
        assert str(caught.value) in report
    if outcome == "inconclusive":
        assert "Historical good: Need at least 2 independent CI jobs, got 1" in report
        assert "Independent jobs: 1 / 5 / 5" in report
        assert "100.00 ms / 102.00 ms / 102.00 ms" in report
    if outcome == "ci_failure":
        assert "CI build/test failure" in report
    if submissions and outcome != "ci_error":
        assert (
            "[Try results](https://treeherder.mozilla.org/jobs?repo=try&revision=candidate)"
            in report
        )
    if submissions and outcome not in {"ci_error", "deferred_pending"}:
        # Local and deployed runs alike link the candidate as pushed to Try.
        assert "try/rev/" + "d" * 40 in report
    if fixed:
        assert result.winning_attempt == 1
        assert (repo / source).read_text() == "candidate 1\n"
        assert (artifacts / "verified/fix.patch").exists()
        assert (artifacts / "verified/description.txt").read_text() == report
        assert (artifacts / "changes/changes.patch").exists()
        assert len(submissions) == 1
    else:
        pushed_outcomes = {
            "exhausted",
            "ci_error",
            "ci_failure",
            "inconclusive",
            "deferred_pending",
            "deferred_timeout",
        }
        assert not (artifacts / "verified/fix.patch").exists()
        assert len(submissions) == (1 if outcome in pushed_outcomes else 0)
        # changes/ holds the candidate that was pushed, recovered or not.
        assert (artifacts / "changes/changes.patch").exists() == (
            outcome in pushed_outcomes
        )
        if outcome not in pushed_outcomes:
            assert entry.git(repo, "rev-parse", "HEAD") == base
            assert entry.git(repo, "status", "--porcelain") == ""
    assert len(entry.git(repo, "worktree", "list").splitlines()) == 1


@pytest.mark.parametrize("harness_changes_allowed", [False, True])
@pytest.mark.parametrize(
    "path,harness_edit,always_protected",
    [
        ("layout.cpp", False, False),
        ("testing/raptor/browsertime/speedometer3.js", True, False),
        ("testing/talos/talos/tests/test.html", True, False),
        ("taskcluster/ci/test/raptor.yml", True, False),
        ("tools/tryselect/selectors/perf.py", True, False),
        ("testing/mozharness/mozharness/mozilla/testing/raptor.py", True, False),
        ("testing/performance/perftest_record.js", True, False),
        ("python/mozperftest/mozperftest/runner.py", True, False),
        (".taskcluster.yml", True, False),
        ("try_task_config.json", False, True),
        ("third_party/webkit/PerformanceTests/Speedometer3/index.html", False, True),
    ],
)
def test_harness_exception_keeps_benchmarks_and_try_configuration_protected(
    tmp_path, path, harness_edit, always_protected, harness_changes_allowed
):
    entry.git(tmp_path, "init")
    source = tmp_path / path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("baseline\n")
    entry.changes.commit_all(tmp_path, "base")
    base = entry.git(tmp_path, "rev-parse", "HEAD")
    source.write_text("candidate\n")
    if always_protected or (harness_edit and not harness_changes_allowed):
        with pytest.raises(EvidenceError, match="benchmark/CI configuration"):
            entry.validate_candidate(
                tmp_path, base, harness_changes_allowed=harness_changes_allowed
            )
    else:
        entry.validate_candidate(
            tmp_path, base, harness_changes_allowed=harness_changes_allowed
        )


@pytest.mark.parametrize("filename", ["test.txt", "tést.txt"])
def test_protected_source_cannot_be_changed_by_renaming_it(tmp_path, filename):
    entry.git(tmp_path, "init")
    source = tmp_path / "testing/raptor" / filename
    source.parent.mkdir(parents=True)
    source.write_text("benchmark input\n")
    entry.changes.commit_all(tmp_path, "base")
    base = entry.git(tmp_path, "rev-parse", "HEAD")
    entry.git(tmp_path, "mv", f"testing/raptor/{filename}", "moved.txt")
    with pytest.raises(EvidenceError, match="benchmark/CI configuration"):
        entry.validate_candidate(tmp_path, base)


async def test_out_of_scope_alert_stops_before_checkout_model_and_try(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    from hackbot_agents.perf_regression.evidence import OutOfScope

    monkeypatch.setenv("RUN_ID", "run")
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.delenv("RESULTS_POLICY_URL", raising=False)
    ctx = HackbotContext.from_config_obj(
        HackbotConfig(
            source={
                "repo_url": "https://example.com/firefox.git",
                "checkout_path": tmp_path,
            }
        )
    )
    monkeypatch.setattr(
        entry,
        "Inputs",
        lambda: SimpleNamespace(
            bug_id=1,
            alert_id=None,
            perf_validation_state_url=None,
            suites=["speedometer3"],
        ),
    )
    collect = AsyncMock(
        side_effect=OutOfScope("The alert has no regression in speedometer3")
    )
    monkeypatch.setattr(entry.EvidenceClient, "collect", collect)
    monkeypatch.setattr(entry, "propose", AsyncMock())
    monkeypatch.setattr(entry, "TryClient", AsyncMock())
    prepare = AsyncMock()
    monkeypatch.setattr(HackbotContext, "prepare_repo", prepare)

    result = await entry.main(ctx)

    assert result.status == "out_of_scope"
    assert collect.await_args.args[-1] == ["speedometer3"]
    entry.propose.assert_not_awaited()
    entry.TryClient.assert_not_called()
    prepare.assert_not_awaited()
    assert _finish(ctx, result) == 0
    report = (ctx.run_artifacts_dir / "investigation/report.md").read_text()
    assert "no regression in speedometer3" in report


async def test_local_runs_push_the_candidate_with_the_developers_token(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    from lando_client import LandoClient

    entry.git(tmp_path, "init")
    (tmp_path / "layout.cpp").write_text("baseline\n")
    entry.changes.commit_all(tmp_path, "base")
    base = entry.git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "layout.cpp").write_text("candidate\n")
    entry.changes.commit_all(tmp_path, "Bug 1 - candidate")
    monkeypatch.setenv("LANDO_ACCESS_TOKEN", "developer-token")
    submit = AsyncMock(return_value=77)
    monkeypatch.setattr(LandoClient, "submit_try_patches", submit)

    submission = await entry.push_to_try(tmp_path, base, ["test-speedometer3"])

    patches, push_base = submit.await_args.args
    assert push_base == base
    assert len(patches) == 2  # The candidate, then its try configuration.
    assert b"candidate" in b64decode(patches[0])
    config = b64decode(patches[1]).decode()
    assert '"rebuild": 5' in config and "test-speedometer3" in config
    assert submission["status_url"].endswith("/landing_jobs/77/")
    assert submission["repetitions"] == 5
