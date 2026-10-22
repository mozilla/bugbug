import sentry_sdk
from app.action_handlers import ApplyContext, feedback_handler
from sentry_sdk.transport import Transport


async def test_report_is_sent_to_sentry_grouped_by_title():
    events = []

    class _Capture(Transport):
        def capture_envelope(self, envelope):
            events.extend(item.payload.json for item in envelope.items)

    sentry_sdk.init(dsn="https://key@sentry.example/1", transport=_Capture())

    async def download(_key):
        raise AssertionError("Feedback reports do not use artifacts")

    try:
        result = await feedback_handler.ReportProblemHandler().apply(
            {
                "severity": "warning",
                "title": "No tool to read bug history",
                "message": "Needed it to see who changed the priority.",
            },
            ApplyContext(run_id="run-1", agent="bug-fix", download_artifact=download),
        )
    finally:
        sentry_sdk.get_client().close()

    assert result.status == "applied"
    (event,) = events
    assert event["level"] == "warning"
    assert event["tags"] == {
        "kind": "agent_feedback",
        "agent": "bug-fix",
        "run_id": "run-1",
    }
    assert event["fingerprint"] == ["agent-feedback", "No tool to read bug history"]
    assert event["extra"]["message"] == "Needed it to see who changed the priority."
