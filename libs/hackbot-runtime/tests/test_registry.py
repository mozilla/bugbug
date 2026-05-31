"""Tests for the action registry and schema derivation."""

from hackbot_runtime.actions import ActionInputError, get_actions
from hackbot_runtime.actions.registry import ActionDefinition

_BUGZILLA_TYPES = {
    "bugzilla.update_bug",
    "bugzilla.add_comment",
    "bugzilla.add_attachment",
    "bugzilla.create_bug",
}


def test_get_actions_returns_all():
    assert {a.type for a in get_actions()} == _BUGZILLA_TYPES


def test_get_actions_filtered():
    got = get_actions(["bugzilla.update_bug", "bugzilla.add_comment"])
    assert {a.type for a in got} == {"bugzilla.update_bug", "bugzilla.add_comment"}


def test_action_input_error_is_exception():
    assert issubclass(ActionInputError, Exception)


def test_input_schema_excludes_recorder_and_keeps_descriptions():
    update = next(a for a in get_actions() if a.type == "bugzilla.update_bug")
    schema = update.input_schema
    props = schema["properties"]
    assert "recorder" not in props
    assert set(props) == {"bug_id", "changes", "reasoning"}
    assert set(schema["required"]) == {"bug_id", "changes", "reasoning"}
    assert props["bug_id"]["description"]


def test_input_schema_marks_optional_params():
    comment = next(a for a in get_actions() if a.type == "bugzilla.add_comment")
    # is_private has a default -> not required.
    assert "is_private" not in comment.input_schema.get("required", [])


def test_input_schema_is_cached():
    defn = ActionDefinition(
        type="x.y", description="d", handler=get_actions()[0].handler
    )
    assert defn.input_schema is defn.input_schema
