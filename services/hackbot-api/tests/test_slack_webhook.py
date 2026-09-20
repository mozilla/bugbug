import json

from app.auth import require_slack_signature
from app.main import app
from app.routers import slack


class _FakeHackbotClient:
    def __init__(self):
        self.calls = []

    async def trigger_run(self, agent_name, inputs):
        self.calls.append((agent_name, inputs))


def test_start_agent_run_action_triggers_run(client, monkeypatch):
    api_client = _FakeHackbotClient()
    app.dependency_overrides[require_slack_signature] = lambda: None
    monkeypatch.setattr(slack, "get_hackbot_client", lambda: api_client)
    payload = {
        "type": "block_actions",
        "user": {"id": "U123", "username": "user"},
        "actions": [
            {
                "action_id": "start",
                "value": json.dumps(
                    {
                        "type": "start_agent_run",
                        "agent_name": "bug-fix",
                        "params": {"bug_id": 123},
                    }
                ),
            }
        ],
        "trigger_id": "trigger-123",
    }

    response = client.post(
        "/webhooks/slack/interactions",
        data={"payload": json.dumps(payload)},
    )

    assert response.status_code == 200
    assert api_client.calls == [("bug-fix", {"bug_id": 123})]
