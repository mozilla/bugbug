import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from app.schemas import (
    AutowebcompatDiagnosisInputs,
    AutowebcompatReproInputs,
    BugFixInputs,
    BuildRepairInputs,
    FrontendTriageInputs,
    TestPlanGeneratorInputs,
    TestRepairInputs,
)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    job_name: str
    input_schema: type[BaseModel]
    # Optional override for the rare agent whose env vars don't map 1:1 from
    # its input schema. Defaults to ``model_to_env`` (field -> UPPER_SNAKE env).
    build_env: Callable[[BaseModel], dict[str, str]] | None = None
    # Whether this agent's recorded actions are applied AUTOMATICALLY when a run
    # succeeds. Off by default: actions are still recorded and can always be
    # applied manually from the UI; only opted-in agents auto-apply.
    auto_apply_actions: bool = False
    # Whether auto-apply additionally needs the run's own say-so: `findings.auto_apply`
    # must be True, or the actions are recorded and held. Set this for agents that
    # judge their results one run at a time, since only the agent knows how sure it
    # was; this is where that verdict is honored. Fails closed, so a run that reports
    # no verdict never qualifies.
    auto_apply_requires_consent: bool = False


def model_to_env(inputs: BaseModel) -> dict[str, str]:
    """Serialise validated inputs into Cloud Run Job env overrides.

    Each schema field maps to an upper-cased env var (``bug_id`` -> ``BUG_ID``);
    ``None`` fields are skipped, and the agent reads them back via
    ``pydantic_settings.BaseSettings`` (which upper-cases field names by
    default). Lists/dicts are JSON-encoded. Deploy-time constants (e.g. the
    broker loopback URL) are NOT inputs — they belong in the Job's static env
    config, not here.
    """
    env: dict[str, str] = {}
    for name, value in inputs.model_dump(mode="json").items():
        if value is None:
            continue
        if isinstance(value, str):
            env[name.upper()] = value
        elif isinstance(value, (list, dict)):
            env[name.upper()] = json.dumps(value)
        else:
            env[name.upper()] = str(value)
    return env


AGENT_REGISTRY: dict[str, AgentSpec] = {
    "bug-fix": AgentSpec(
        name="bug-fix",
        description="Investigate a Bugzilla bug and produce a candidate fix patch against the Firefox source tree.",
        job_name="hackbot-agent-bug-fix",
        input_schema=BugFixInputs,
        auto_apply_actions=True,
    ),
    "autowebcompat-repro": AgentSpec(
        name="autowebcompat-repro",
        description=(
            "Reproduce a Firefox web-compatibility issue in headless Firefox "
            "(from inline report text or a Bugzilla bug id) and return findings."
        ),
        job_name="hackbot-agent-autowebcompat-repro",
        input_schema=AutowebcompatReproInputs,
    ),
    "autowebcompat-diagnosis": AgentSpec(
        name="autowebcompat-diagnosis",
        description=(
            "Diagnose the root cause of a Firefox web-compatibility "
            "issue by comparing Firefox and Chrome, and "
            "produce a reduced HTML testcase."
        ),
        job_name="hackbot-agent-autowebcompat-diagnosis",
        input_schema=AutowebcompatDiagnosisInputs,
    ),
    "build-repair": AgentSpec(
        name="build-repair",
        description="Analyze a Firefox build failure at a specific commit and produce a candidate fix patch.",
        job_name="hackbot-agent-build-repair",
        input_schema=BuildRepairInputs,
    ),
    "frontend-triage": AgentSpec(
        name="frontend-triage",
        description=(
            "Triage a user-facing Firefox bug (read-only): desktop frontend, "
            "Firefox for Android, the Windows installer, or the application "
            "updater. Produce a root-cause analysis and proposed fix plan."
        ),
        job_name="hackbot-agent-frontend-triage",
        input_schema=FrontendTriageInputs,
        # Triage results reach a real bug unattended, so only the ones the agent
        # marked `auto_apply` qualify. Everything else stays for manual apply.
        auto_apply_actions=True,
        auto_apply_requires_consent=True,
    ),
    "test-repair": AgentSpec(
        name="test-repair",
        description=(
            "Analyze a Firefox CI test failure: classify it as a regression or an "
            "intermittent, blame the culprit commit, and propose a fix patch for "
            "regressions."
        ),
        job_name="hackbot-agent-test-repair",
        input_schema=TestRepairInputs,
        auto_apply_actions=True,
    ),
    "test-plan-generator": AgentSpec(
        name="test-plan-generator",
        description=(
            "Generate Firefox QA test cases from feature details (up to 20 test cases), "
            "run them in Firefox through DevTools MCP, and report pass/fail/unsuitable results."
        ),
        job_name="hackbot-agent-test-plan-generator",
        input_schema=TestPlanGeneratorInputs,
    ),
}
