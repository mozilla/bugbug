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
from sqlalchemy import Insert, Update  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.exc import (  # noqa: E402
    IntegrityError,
    MultipleResultsFound,
    NoResultFound,
)


def _statement_values(stmt) -> dict:
    """The column values a statement would write, as a plain dict.

    Compiled params also carry the WHERE clause's binds, which SQLAlchemy names
    with a suffix (`run_id_1`), so matching column names exactly keeps only the
    values being written.
    """
    params = stmt.compile(dialect=postgresql.dialect()).params
    columns = {column.name for column in Run.__table__.columns}
    return {name: value for name, value in params.items() if name in columns}


def _skips_conflicts(stmt) -> bool:
    """Whether the INSERT asked Postgres to skip a unique-index conflict."""
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    return "ON CONFLICT" in sql and "DO NOTHING" in sql


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
    sets up "a run already holds this dedupe key" without a Postgres.

    An INSERT ... RETURNING answers with the row it would have written, kept as
    `added` so a test can assert on what the handler built and on what it does
    to the row afterwards. Set `conflict` to have the unique index turn that
    insert away: it returns nothing if the statement asked for DO NOTHING, and
    raises IntegrityError if it did not, which is what Postgres would do.
    """

    def __init__(self, matches: list | None = None):
        self.matches = matches or []
        self.added = None
        self.conflict = False
        self.statements: list = []
        self.commits = 0
        self.rollbacks = 0

    @property
    def stmt(self):
        """The last statement executed, for tests that assert on one query."""
        return self.statements[-1] if self.statements else None

    async def scalar(self, stmt):
        return (await self.execute(stmt)).scalars().first()

    async def execute(self, stmt):
        self.statements.append(stmt)

        if isinstance(stmt, Update):
            for column, value in _statement_values(stmt).items():
                setattr(self.added, column, value)
            return _Result([])

        if isinstance(stmt, Insert):
            if self.conflict:
                if not _skips_conflicts(stmt):
                    raise IntegrityError(
                        "INSERT INTO runs", {}, Exception("uq_runs_dedupe_key")
                    )
                return _Result([])
            self.added = Run(**_statement_values(stmt))
            return _Result([self.added])

        # Only queries over `runs` return rows; anything else a handler runs
        # for effect gets an empty result.
        if Run.__table__ in stmt.get_final_froms():
            return _Result(self.matches)
        return _Result([])

    async def commit(self):
        self.commits += 1

    async def rollback(self):
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
