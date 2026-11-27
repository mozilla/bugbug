# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from unittest.mock import MagicMock

import pytest
from app import ci_events, consumer
from app.config import settings


@pytest.mark.parametrize("state", ["completed", "failed", "exception"])
def test_forward_try_terminal_event(monkeypatch, state):
    monkeypatch.setattr(settings, "ci_events_topic", "projects/test/topics/ci")
    monkeypatch.setattr(settings, "dry_run", False)
    publisher = MagicMock()
    monkeypatch.setattr(ci_events, "publisher", lambda: publisher)
    ci_events.forward(
        {
            "task": {"tags": {"project": "try"}},
            "status": {"state": state, "taskGroupId": "group", "taskId": "task"},
        }
    )
    assert publisher.publish.call_args.kwargs == {"task_group_id": "group"}
    publisher.publish.return_value.result.assert_called_once_with(timeout=30)


@pytest.mark.parametrize(
    "project,state,dry_run",
    [
        ("autoland", "completed", False),
        ("try", "running", False),
        ("try", "completed", True),
    ],
)
def test_do_not_forward_unrelated_events(monkeypatch, project, state, dry_run):
    monkeypatch.setattr(settings, "ci_events_topic", "projects/test/topics/ci")
    monkeypatch.setattr(settings, "dry_run", dry_run)
    publisher = MagicMock()
    monkeypatch.setattr(ci_events, "publisher", lambda: publisher)
    ci_events.forward(
        {
            "task": {"tags": {"project": project}},
            "status": {"state": state, "taskGroupId": "group"},
        }
    )
    publisher.publish.assert_not_called()


def test_completion_does_not_trigger_failure_repair(monkeypatch):
    forward, process = MagicMock(), MagicMock()
    monkeypatch.setattr(ci_events, "forward", forward)
    monkeypatch.setattr(consumer, "process", process)
    executor, forwarder = MagicMock(), MagicMock()
    executor.submit.side_effect = lambda fn, body: fn(body)
    forwarder.submit.side_effect = lambda fn, body: fn(body)
    msg = MagicMock()
    consumer.make_handler(executor, forwarder)({"status": {"state": "completed"}}, msg)
    forward.assert_called_once()
    process.assert_not_called()
    # Completions never occupy the failure-repair pool.
    executor.submit.assert_not_called()
    msg.ack.assert_called_once()


def test_forward_failure_does_not_disable_repair(monkeypatch):
    monkeypatch.setattr(ci_events, "forward", MagicMock(side_effect=RuntimeError))
    process = MagicMock()
    monkeypatch.setattr(consumer, "process", process)
    executor, forwarder = MagicMock(), MagicMock()
    executor.submit.side_effect = lambda fn, body: fn(body)
    forwarder.submit.side_effect = lambda fn, body: fn(body)
    consumer.make_handler(executor, forwarder)(
        {"status": {"state": "failed"}}, MagicMock()
    )
    process.assert_called_once()


def test_subscribes_to_completion_and_exception_only_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "ci_events_topic", "projects/test/topics/ci")
    assert {
        q.exchange.name.rsplit("/", 1)[-1]: q.routing_key
        for q in consumer._build_queues("test")
    } == {
        "task-failed": "#",
        "task-completed": "route.tc-treeherder.v2.try.#",
        "task-exception": "route.tc-treeherder.v2.try.#",
    }
