import os

# app.config builds a pydantic Settings() at import time, which requires a set
# of env vars. Provide dummies so the app package is importable under test.
_DUMMY_ENV = {
    "cloud_sql_instance": "test:test:test",
    "db_user": "test",
    "db_pass": "test",
    "db_name": "test",
    "external_api_key": "test",
    "internal_api_key": "test",
    "cloud_tasks_project": "test",
    "cloud_tasks_location": "test",
    "cloud_tasks_queue": "test",
    "worker_url": "https://worker.test",
    "phabricator_url": "https://phabricator.test",
    "phabricator_api_key": "test",
    "bugzilla_url": "https://bugzilla.test",
    "bugzilla_api_key": "test",
}

for key, value in _DUMMY_ENV.items():
    os.environ.setdefault(key, value)
