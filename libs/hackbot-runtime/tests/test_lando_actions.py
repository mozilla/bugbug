"""Tests for recording a backout."""

from hackbot_runtime.actions import lando
from hackbot_runtime.actions.recorder import ActionsRecorder


def test_record_backout_records_the_commit():
    rec = ActionsRecorder()

    action = lando.record_backout(
        rec, lando_repo="firefox-autoland", commit="c" * 40, reasoning="regression"
    )

    action.pop("action_id")
    assert rec.actions == [action]
    assert action == {
        "type": "lando.backout",
        "params": {"lando_repo": "firefox-autoland", "commit": "c" * 40},
        "reasoning": "regression",
    }
