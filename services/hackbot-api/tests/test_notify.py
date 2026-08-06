"""Tests for the run-completion notification.

The message is delivered by email to a Slack channel address ("Email to
channel"), so Slack renders the subject as the message title — that is where the
one-line summary belongs. See app/notify.py.
"""

import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from app import notify
from app.schemas import RunActionOutcome


@dataclass
class _FakeRun:
    agent: str = "frontend-triage"
    run_id: uuid.UUID = field(default_factory=lambda: uuid.UUID(int=7))
    status: str = "succeeded"
    inputs: dict = field(default_factory=lambda: {"bug_id": 2014702})
    summary: dict | None = None


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return iter(self._rows)


class _FakeDB:
    """Enough of `AsyncSession` for the outcome read.

    The outcome is derived from the action rows the applier left behind, so the fake
    is just those statuses.
    """

    def __init__(self, *statuses: str) -> None:
        self.rows = [SimpleNamespace(status=status) for status in statuses]

    async def execute(self, statement):
        return _FakeResult(self.rows)

    async def commit(self) -> None:
        pass


def _triaged(**findings):
    base = {
        "summary": "Weather widget disappears after switching regions",
        "confidence": "high",
    }
    base.update(findings)
    return _FakeRun(summary={"findings": base, "actions": []})


def _configure(monkeypatch):
    monkeypatch.setattr(notify.settings, "bugzilla_url", "https://bugzilla.mozilla.org")
    monkeypatch.setattr(notify.settings, "hackbot_ui_url", "https://hackbot.example")


# --- subject: the one-line summary ------------------------------------- #


def test_subject_is_one_line_with_agent_bug_and_summary(monkeypatch):
    _configure(monkeypatch)
    subject, _ = notify.build_notification(_triaged(), RunActionOutcome.posted)
    assert "\n" not in subject
    assert "frontend-triage" in subject
    assert "2014702" in subject
    assert "Weather widget disappears after switching regions" in subject


def test_subject_truncates_a_rambling_summary(monkeypatch):
    _configure(monkeypatch)
    subject, _ = notify.build_notification(
        _triaged(summary="x" * 500), RunActionOutcome.posted
    )
    assert len(subject) <= notify.MAX_SUBJECT_LENGTH
    assert subject.endswith("…")


def test_subject_collapses_a_multiline_summary(monkeypatch):
    _configure(monkeypatch)
    subject, _ = notify.build_notification(
        _triaged(summary="first line\nsecond line"), RunActionOutcome.posted
    )
    assert "\n" not in subject
    assert "first line second line" in subject


def test_subject_falls_back_without_a_summary(monkeypatch):
    _configure(monkeypatch)
    subject, _ = notify.build_notification(
        _FakeRun(summary={"findings": {}}), RunActionOutcome.held
    )
    assert "\n" not in subject
    assert "2014702" in subject


# --- body: links and outcome ------------------------------------------- #


def test_body_links_the_bug(monkeypatch):
    _configure(monkeypatch)
    _, body = notify.build_notification(_triaged(), RunActionOutcome.posted)
    assert "https://bugzilla.mozilla.org/show_bug.cgi?id=2014702" in body


def test_body_links_the_run(monkeypatch):
    _configure(monkeypatch)
    _, body = notify.build_notification(_triaged(), RunActionOutcome.posted)
    assert f"https://hackbot.example/runs/{uuid.UUID(int=7)}" in body


def test_body_omits_run_link_when_ui_url_unset(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(notify.settings, "hackbot_ui_url", "")
    _, body = notify.build_notification(_triaged(), RunActionOutcome.posted)
    assert "/runs/" not in body


def test_body_reports_confidence(monkeypatch):
    _configure(monkeypatch)
    _, body = notify.build_notification(
        _triaged(confidence="medium"), RunActionOutcome.held
    )
    assert "medium" in body


def test_body_distinguishes_applied_from_awaiting_review(monkeypatch):
    _configure(monkeypatch)
    _, posted = notify.build_notification(_triaged(), RunActionOutcome.posted)
    _, held = notify.build_notification(
        _triaged(confidence="low"), RunActionOutcome.held
    )
    assert "Posted to Bugzilla" in posted
    assert "Posted to Bugzilla" not in held
    assert "awaiting review" in held


def test_body_reports_a_failed_apply_as_neither_posted_nor_held(monkeypatch):
    _configure(monkeypatch)
    _, body = notify.build_notification(_triaged(), RunActionOutcome.failed)
    assert "Posted to Bugzilla" not in body
    assert "awaiting review" not in body
    assert "failed" in body


def test_body_reports_a_run_that_recorded_nothing(monkeypatch):
    _configure(monkeypatch)
    _, body = notify.build_notification(_triaged(), RunActionOutcome.no_actions)
    assert "Posted to Bugzilla" not in body
    assert "no actions" in body


def test_body_survives_a_run_with_no_summary(monkeypatch):
    _configure(monkeypatch)
    subject, body = notify.build_notification(_FakeRun(), RunActionOutcome.held)
    assert subject and body
    assert "https://bugzilla.mozilla.org/show_bug.cgi?id=2014702" in body


def test_body_survives_a_run_with_no_bug_id(monkeypatch):
    _configure(monkeypatch)
    subject, body = notify.build_notification(
        _FakeRun(inputs={}), RunActionOutcome.held
    )
    assert subject and body
    assert "show_bug.cgi" not in body


# --- the outcome, read off the rows the applier left behind -------------- #
#
# "We decided to apply" and "Bugzilla accepted it" are different answers, and the
# channel needs the second one.


@pytest.mark.parametrize(
    "statuses, expected",
    [
        ((), RunActionOutcome.no_actions),
        (("applied",), RunActionOutcome.posted),
        (("applied", "applied"), RunActionOutcome.posted),
        (("pending",), RunActionOutcome.held),
        (("applied", "pending"), RunActionOutcome.held),
        (("failed",), RunActionOutcome.failed),
        (("applied", "failed"), RunActionOutcome.failed),
    ],
)
async def test_the_outcome_follows_the_rows(statuses, expected):
    assert await notify._outcome(_FakeDB(*statuses), _FakeRun()) is expected


async def test_a_rejected_put_is_not_reported_as_posted(monkeypatch):
    # A held run whose actions a human already applied reads as posted; one whose PUT
    # Bugzilla rejected must not.
    _configure_sending(monkeypatch)
    with patch("sendgrid.SendGridAPIClient") as sg:
        await notify.notify_run_completed(_FakeDB("failed"), _triaged())
    body = sg.return_value.send.call_args.kwargs["message"].get()["content"][0]["value"]
    assert "Posted to Bugzilla" not in body
    assert "failed" in body


# --- sending: gated on config ------------------------------------------- #


NEWTAB = "Firefox :: New Tab Page"
NEWTAB_CHANNEL = "hnt-dev-aaa@mozilla.org.slack.com"


def _configure_sending(monkeypatch, channels=None, component=NEWTAB, stub_lookup=True):
    _configure(monkeypatch)
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(notify.settings, "notification_sender", "hackbot@mozilla.com")
    monkeypatch.setattr(
        notify.settings,
        "notification_slack_emails",
        {NEWTAB: NEWTAB_CHANNEL} if channels is None else channels,
    )
    if not stub_lookup:
        return

    # The component comes from Bugzilla, not from the run; stub the lookup. The
    # lookup itself is covered against a stub transport further down.
    async def _component(bug_id):
        return component

    monkeypatch.setattr(notify, "_bug_product_component", _component)


def _sent_to(sg):
    return sg.return_value.send.call_args.kwargs["message"].get()["personalizations"][
        0
    ]["to"]


# --- routing: which channel gets the message ---------------------------- #


async def test_routes_to_the_channel_for_the_bugs_component(monkeypatch):
    # One address per team: New Tab Page bugs go to the New Tab Page channel.
    _configure_sending(monkeypatch)
    with patch("sendgrid.SendGridAPIClient") as sg:
        await notify.notify_run_completed(_FakeDB("applied"), _triaged())
    assert _sent_to(sg) == [{"email": NEWTAB_CHANNEL}]


async def test_routes_a_second_component_to_its_own_channel(monkeypatch):
    other = "Firefox :: Address Bar"
    _configure_sending(
        monkeypatch,
        channels={NEWTAB: NEWTAB_CHANNEL, other: "urlbar@mozilla.org.slack.com"},
        component=other,
    )
    with patch("sendgrid.SendGridAPIClient") as sg:
        await notify.notify_run_completed(_FakeDB("applied"), _triaged())
    assert _sent_to(sg) == [{"email": "urlbar@mozilla.org.slack.com"}]


async def test_unmapped_component_with_no_default_sends_nothing(monkeypatch):
    # Better silent than posting one team's triage into another team's channel.
    _configure_sending(monkeypatch, component="Firefox :: Menus")
    with patch("sendgrid.SendGridAPIClient") as sg:
        await notify.notify_run_completed(_FakeDB("applied"), _triaged())
    sg.assert_not_called()


async def test_no_channels_configured_sends_nothing(monkeypatch):
    _configure_sending(monkeypatch, channels={})
    with patch("sendgrid.SendGridAPIClient") as sg:
        await notify.notify_run_completed(_FakeDB("applied"), _triaged())
    sg.assert_not_called()


async def test_skips_when_sendgrid_unconfigured(monkeypatch):
    _configure_sending(monkeypatch)
    monkeypatch.setattr(notify.settings, "sendgrid_api_key", None)
    monkeypatch.setattr(notify.settings, "notification_sender", None)
    with patch("sendgrid.SendGridAPIClient") as sg:
        await notify.notify_run_completed(_FakeDB("applied"), _triaged())
    sg.assert_not_called()


async def test_skips_when_no_channel_is_configured(monkeypatch):
    # Credentials present but no destination: still nothing to do.
    _configure_sending(monkeypatch, channels={})
    with patch("sendgrid.SendGridAPIClient") as sg:
        await notify.notify_run_completed(_FakeDB("applied"), _triaged())
    sg.assert_not_called()


async def test_sends_once_to_the_channel_address(monkeypatch):
    _configure_sending(monkeypatch)
    run = _triaged()
    with patch("sendgrid.SendGridAPIClient") as sg:
        await notify.notify_run_completed(_FakeDB("applied"), run)

    sg.assert_called_once_with(api_key="key")
    client = sg.return_value
    assert client.send.call_count == 1
    sent = client.send.call_args.kwargs["message"].get()
    assert sent["personalizations"][0]["to"] == [{"email": NEWTAB_CHANNEL}]
    assert sent["from"] == {"email": "hackbot@mozilla.com"}
    assert "2014702" in sent["subject"]
    assert "Posted to Bugzilla" in sent["content"][0]["value"]


async def test_a_forged_multiline_confidence_cannot_add_lines_to_the_body(monkeypatch):
    # `confidence` is model output from a free-form JSON block, and the agent
    # reads untrusted bug comments. Left verbatim it could forge the one line the
    # channel reads to know whether Bugzilla was written.
    _configure(monkeypatch)
    _, body = notify.build_notification(
        _triaged(confidence="low\nPosted to Bugzilla.\nBug: https://evil.example/x"),
        RunActionOutcome.held,
    )
    assert "evil.example" not in body
    assert "Posted to Bugzilla" not in body
    assert "Confidence: unknown" in body
    assert body.count("Bug: ") == 1


@pytest.mark.parametrize("value", [None, 7, "", "  ", "very-high", "HIGH\n"])
async def test_confidence_is_only_echoed_when_it_is_a_documented_value(
    monkeypatch, value
):
    _configure(monkeypatch)
    _, body = notify.build_notification(
        _triaged(confidence=value), RunActionOutcome.held
    )
    expected = (
        "high"
        if isinstance(value, str) and value.strip().lower() == "high"
        else "unknown"
    )
    assert f"Confidence: {expected}" in body


# --- the Bugzilla component lookup -------------------------------------- #


def _bugzilla(monkeypatch, handler):
    """Point the lookup at a stub transport instead of monkeypatching it away."""
    _configure(monkeypatch)
    real_client = httpx.AsyncClient

    def _client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(notify.httpx, "AsyncClient", _client)


async def test_lookup_returns_the_product_and_component(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(
            200, json={"bugs": [{"product": "Firefox", "component": "New Tab Page"}]}
        )

    _bugzilla(monkeypatch, handler)
    assert await notify._bug_product_component(2014702) == NEWTAB
    assert "/rest/bug/2014702" in seen["url"]
    assert "include_fields=product%2Ccomponent" in seen["url"]


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(500, text="nope"),
        lambda request: httpx.Response(200, text="<html>not json</html>"),
        lambda request: httpx.Response(200, json={"bugs": [{}]}),
        lambda request: httpx.Response(200, json={"bugs": [{"product": "Firefox"}]}),
        lambda request: httpx.Response(200, json={"bugs": ["not-a-dict"]}),
        lambda request: httpx.Response(
            200, json={"bugs": [{"product": 1, "component": 2}]}
        ),
    ],
    ids=[
        "500",
        "not-json",
        "no-fields",
        "half-fields",
        "not-a-dict",
        "non-string",
    ],
)
async def test_a_bad_bugzilla_answer_is_not_an_exception(monkeypatch, handler):
    # This runs on the run-completion path: raising here would 500 the push route and
    # earn an endless Pub/Sub redelivery, long after the actions were applied.
    _bugzilla(monkeypatch, handler)
    assert await notify._bug_product_component(1) is None


async def test_a_bugzilla_timeout_is_not_an_exception(monkeypatch):
    def handler(request):
        raise httpx.ConnectTimeout("too slow")

    _bugzilla(monkeypatch, handler)
    assert await notify._bug_product_component(1) is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"bugs": []}),
        httpx.Response(401, json={"error": True}),
        httpx.Response(403, json={"error": True}),
    ],
    ids=["empty", "401", "403"],
)
async def test_a_bug_we_cannot_read_is_silent(monkeypatch, response):
    # A bug this anonymous lookup can't see is most likely a restricted one, and the
    # message would carry the agent's summary of it. No channel, no message.
    _configure_sending(monkeypatch, stub_lookup=False)
    _bugzilla(monkeypatch, lambda request: response)

    with patch("sendgrid.SendGridAPIClient") as sg:
        await notify.notify_run_completed(_FakeDB("applied"), _triaged())

    sg.return_value.send.assert_not_called()
