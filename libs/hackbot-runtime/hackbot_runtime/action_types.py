from enum import StrEnum


class ActionType(StrEnum):
    BUGZILLA_UPDATE_BUG = "bugzilla.update_bug"
    BUGZILLA_ADD_COMMENT = "bugzilla.add_comment"
    BUGZILLA_ADD_ATTACHMENT = "bugzilla.add_attachment"
    BUGZILLA_CREATE_BUG = "bugzilla.create_bug"
    PHABRICATOR_SUBMIT_PATCH = "phabricator.submit_patch"
    PHABRICATOR_UPDATE_PATCH = "phabricator.update_patch"
    PHABRICATOR_ADD_COMMENT = "phabricator.add_comment"
    TESTRAIL_SUBMIT_TEST_PLAN = "testrail.submit_test_plan"
    SLACK_POST_MESSAGE = "slack.post_message"
    EMAIL_SEND = "email.send"
    TRY_SERVER_PUSH = "try_server.push"
    FEEDBACK_REPORT_PROBLEM = "feedback.report_problem"
    AGENTS_START_RUN = "agents.start_run"
