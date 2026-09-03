from app import taskcluster

DECISION = "JAfynVmyQUSOJ1xXIdCuFg"


def _task(parent=DECISION, group=DECISION):
    extra = {"parent": parent} if parent is not None else {}
    return {"taskGroupId": group, "extra": extra}


def test_decision_scheduled_task_is_not_action_scheduled():
    # Everything the push itself schedules is parented to the decision task,
    # which is also the task group.
    assert taskcluster.is_action_scheduled(_task()) is False


def test_backfilled_task_is_action_scheduled():
    # Real shape from bug 6395: the failing build points at the backfill
    # action-callback task while its group is still the original decision task.
    task = _task(parent="Fg-IZvVBTwGr22Fx81om4A")
    assert taskcluster.is_action_scheduled(task) is True


def test_missing_parent_is_not_action_scheduled():
    assert taskcluster.is_action_scheduled(_task(parent=None)) is False
    assert taskcluster.is_action_scheduled({"taskGroupId": DECISION}) is False
    assert taskcluster.is_action_scheduled({}) is False


def test_get_hg_revision_reads_gecko_head_rev():
    task = {"payload": {"env": {"GECKO_HEAD_REV": "abc123"}}}
    assert taskcluster.get_hg_revision(task) == "abc123"
    assert taskcluster.get_hg_revision({"payload": {"env": {}}}) is None
    assert taskcluster.get_hg_revision({}) is None
