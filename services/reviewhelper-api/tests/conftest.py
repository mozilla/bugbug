"""Service tests use dummy configuration and never contact deployed services."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in (
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
    os.environ.setdefault(name, "test")
