import os

# `app.config.settings` is built at import time and requires these values, so
# they must be set before any `app` module is imported by the tests.
for required_setting in (
    "CLOUD_SQL_INSTANCE",
    "DB_USER",
    "DB_PASS",
    "DB_NAME",
    "EXTERNAL_API_KEY",
    "INTERNAL_API_KEY",
    "CLOUD_TASKS_PROJECT",
    "CLOUD_TASKS_LOCATION",
    "CLOUD_TASKS_QUEUE",
    "WORKER_URL",
    "PHABRICATOR_API_KEY",
    "BUGZILLA_URL",
    "BUGZILLA_API_KEY",
):
    os.environ.setdefault(required_setting, "test")

os.environ.setdefault("PHABRICATOR_URL", "https://phabricator.example.com")
