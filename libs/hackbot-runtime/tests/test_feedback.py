import sentry_sdk
from agent_tools.feedback import developer_feedback
from hackbot_runtime import HackbotContext
from sentry_sdk.transport import Transport


async def test_developer_feedback_reports_to_sentry_grouped_by_title(tmp_path):
    events = []

    class _Capture(Transport):
        def capture_envelope(self, envelope):
            events.extend(item.payload.json for item in envelope.items)

    ctx = HackbotContext(run_id="local-test", artifacts_dir=tmp_path / "artifacts")
    sentry_sdk.init(dsn="https://key@sentry.example/1", transport=_Capture())
    try:
        await developer_feedback(
            ctx.feedback,
            severity="warning",
            title="No tool to read bug history",
            message="Needed it to see who changed the priority.",
        )
    finally:
        sentry_sdk.get_client().close()

    (event,) = events
    assert event["level"] == "warning"
    assert event["tags"]["kind"] == "agent_feedback"
    assert event["fingerprint"] == ["agent-feedback", "No tool to read bug history"]
    assert event["extra"]["message"] == "Needed it to see who changed the priority."
