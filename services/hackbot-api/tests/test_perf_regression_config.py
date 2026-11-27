# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Long CI investigations need matching execution and upload lifetimes."""

import base64
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from app import gcs, jobs
from app.agents import AGENT_REGISTRY
from app.config import Settings, settings
from app.schemas import PerfRegressionInputs
from pydantic import ValidationError


def test_registration_cannot_publish_bugzilla_actions():
    spec = AGENT_REGISTRY["perf-regression"]
    assert not spec.auto_apply_actions
    assert not spec.expects_actions
    assert spec.execution_timeout_seconds == 28800


def test_webhook_is_on_for_sheriffed_speedometer3_by_default():
    webhook = Settings().bugzilla_webhook
    assert webhook.perf_regression_enabled
    assert webhook.perf_regression_reporters == ["performance-sheriff-bot@mozilla.tld"]
    assert webhook.perf_regression_suites == ["speedometer3"]


def test_webhook_scope_is_configurable_via_environment(monkeypatch):
    monkeypatch.setenv("BUGZILLA_WEBHOOK_PERF_REGRESSION_ENABLED", "false")
    monkeypatch.setenv(
        "BUGZILLA_WEBHOOK_PERF_REGRESSION_SUITES", '["speedometer3", "jetstream3"]'
    )
    webhook = Settings().bugzilla_webhook
    assert not webhook.perf_regression_enabled
    assert webhook.perf_regression_suites == ["speedometer3", "jetstream3"]


@pytest.mark.parametrize("max_attempts", [0, 2, 3, 4])
def test_schema_bounds_attempts(max_attempts):
    assert PerfRegressionInputs(bug_id=2075214).max_attempts == 1
    with pytest.raises(ValidationError):
        PerfRegressionInputs(bug_id=2075214, max_attempts=max_attempts)
    with pytest.raises(ValidationError):
        PerfRegressionInputs(bug_id=0)


def test_job_override_targets_the_agent_container(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(jobs, "_jobs_client", lambda: client)
    monkeypatch.setattr(jobs, "_job_resource_name", lambda _: "job")
    jobs._trigger_sync("perf-regression", {"BUG_ID": "2075214"}, 28800)
    overrides = client.run_job.call_args.kwargs["request"].overrides
    assert overrides.timeout.total_seconds() == 28800
    assert [c.name for c in overrides.container_overrides] == ["agent"]


def test_upload_policy_lives_as_long_as_job(monkeypatch):
    monkeypatch.setattr(settings, "results_bucket", "test")
    creds = MagicMock(service_account_email="test@example.com")
    creds.sign_bytes.return_value = b"signature"
    monkeypatch.setattr(gcs, "_signing_credentials", lambda: creds)
    result = gcs._generate_post_policy_sync("run", 28800)
    policy = json.loads(base64.b64decode(result["fields"]["policy"]))
    expires = datetime.fromisoformat(policy["expiration"].replace("Z", "+00:00"))
    remaining = (expires - datetime.now(timezone.utc)).total_seconds()
    assert (
        28800 + settings.signed_policy_grace_seconds - 5
        < remaining
        < 28801 + settings.signed_policy_grace_seconds
    )


def test_suites_reach_the_agent_as_json():
    from app.agents import model_to_env

    env = model_to_env(PerfRegressionInputs(bug_id=1, suites=["speedometer3"]))
    assert env["SUITES"] == '["speedometer3"]'
    assert "SUITES" not in model_to_env(PerfRegressionInputs(bug_id=1))
