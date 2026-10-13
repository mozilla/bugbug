import pytest


@pytest.fixture(autouse=True)
def service_environment(monkeypatch):
    for key in (
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
        "PHABRICATOR_URL",
        "PHABRICATOR_API_KEY",
        "BUGZILLA_URL",
        "BUGZILLA_API_KEY",
    ):
        monkeypatch.setenv(key, "test")
