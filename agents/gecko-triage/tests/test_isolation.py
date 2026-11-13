"""Tests for keeping the run's secrets away from the CLI."""

import pytest
from hackbot_agents.gecko_triage import isolation
from hackbot_runtime import AgentError


class FakeLibc:
    def __init__(self, result=0):
        self.calls, self.result = [], result

    def prctl(self, *args):
        self.calls.append(args)
        return self.result


def test_on_linux_the_agent_process_is_made_undumpable(monkeypatch):
    # Other processes of the same user, the CLI's Bash included, then cannot read
    # /proc/<pid>/environ of the agent and the results policy in it.
    libc = FakeLibc()
    monkeypatch.setattr(isolation, "_libc", lambda: libc)
    monkeypatch.setattr(isolation.sys, "platform", "linux")
    isolation.make_undumpable()
    assert libc.calls == [(isolation.PR_SET_DUMPABLE, 0, 0, 0, 0)]


def test_elsewhere_making_the_process_undumpable_does_nothing(monkeypatch):
    libc = FakeLibc()
    monkeypatch.setattr(isolation, "_libc", lambda: libc)
    monkeypatch.setattr(isolation.sys, "platform", "darwin")
    isolation.make_undumpable()
    assert libc.calls == []


def test_a_failed_prctl_fails_the_run(monkeypatch):
    monkeypatch.setattr(isolation, "_libc", lambda: FakeLibc(result=-1))
    monkeypatch.setattr(isolation.sys, "platform", "linux")
    with pytest.raises(AgentError):
        isolation.make_undumpable()


@pytest.mark.parametrize(
    "name",
    [
        "STRIPE_APIKEY",
        "APIKEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AWS_CREDENTIAL",
        "SENTRY_DSN",
        "DB_PASSWD",
        "my_api_key",
        "Some_Secret",
        "results_policy_extra",
        "UPLOAD_POLICY",
    ],
)
def test_every_kind_of_secret_name_is_hidden_from_the_cli(name):
    assert isolation.hidden_env({name: "s"})[name] == ""


@pytest.mark.parametrize(
    "name", ["KEYWORDS", "TOKENIZERS_PARALLELISM", "ANTHROPIC_API_KEY", "HOME"]
)
def test_other_names_reach_the_cli(name):
    assert name not in isolation.hidden_env({name: "s"})
