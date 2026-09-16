import os

# The global Settings embeds required nested models, validated when Settings() is
# built at import: PhabricatorSettings needs a 32-char api_key, WebhookSettings
# needs a secret, BugzillaWebhookSettings needs its own, and SlackSettings needs a
# signing secret. Provide dummies here (before app.config is imported) so the suite
# imports even in tests that don't exercise these. `setdefault` leaves any real env
# value intact.
os.environ.setdefault("PHABRICATOR_API_KEY", "api-" + "a" * 28)
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("BUGZILLA_WEBHOOK_SECRET", "test-bugzilla-webhook-secret")
os.environ.setdefault("BUGZILLA_WEBHOOK_BOT_LOGIN", "hackbot@mozilla.tld")
os.environ.setdefault("BUGZILLA_API_KEY", "test-bugzilla-api-key")
os.environ.setdefault("SLACK_SIGNING_SECRET", "test-signing-secret")

import pytest  # noqa: E402
from app.auth import require_api_key  # noqa: E402
from app.database.connection import get_db  # noqa: E402
from app.database.models import Run  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.exc import (  # noqa: E402
    MultipleResultsFound,
    NoResultFound,
    PendingRollbackError,
)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one(self):
        if not self._rows:
            raise NoResultFound("No row was found when one was required")
        if len(self._rows) > 1:
            raise MultipleResultsFound("Multiple rows were found when one was required")
        return self._rows[0]


class FakeSession:
    """An AsyncSession stand-in that records what a handler did to it.

    Statements are kept so a test can assert on the SQL a handler built, and
    `matches` is what any query against `runs` returns -- which is how a test
    sets up "a run already holds this dedupe key" without a Postgres. Set
    `on_commit` to a callable to interpose on the next commit, which is how a
    test provokes the unique-index conflict the dedupe path is built around.

    A failed commit poisons the session until it is rolled back, as a real one
    does, so a handler that reads after a rejected insert without rolling back
    fails here rather than only against Postgres.
    """

    def __init__(self, matches: list | None = None):
        self.matches = matches or []
        self.added = None
        self.statements: list = []
        self.commits = 0
        self.rollbacks = 0
        self.on_commit = None
        self.needs_rollback = False

    @property
    def stmt(self):
        """The last statement executed, for tests that assert on one query."""
        return self.statements[-1] if self.statements else None

    async def execute(self, stmt):
        if self.needs_rollback:
            raise PendingRollbackError(
                "This Session's transaction has been rolled back due to a "
                "previous exception during flush."
            )

        self.statements.append(stmt)
        # Only queries over `runs` return rows; anything else a handler runs
        # for effect gets an empty result.
        if Run.__table__ in stmt.get_final_froms():
            return _Result(self.matches)
        return _Result([])

    def add(self, obj):
        self.added = obj

    async def commit(self):
        if self.on_commit is not None:
            # One-shot, so a test can fail the claiming commit and still let the
            # handler's later commits through.
            hook, self.on_commit = self.on_commit, None
            try:
                hook()
            except Exception:
                self.needs_rollback = True
                raise
        self.commits += 1

    async def rollback(self):
        self.needs_rollback = False
        self.rollbacks += 1

    async def get(self, model, key):
        return None


@pytest.fixture
def db():
    return FakeSession()


@pytest.fixture
def client(db):
    """A TestClient wired to the fake session, with API-key auth stubbed out."""
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
