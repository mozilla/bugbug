"""Shared storage for the OAuth proxy's state.

The proxy keeps client registrations, in-flight authorizations (PKCE), and
the Google tokens behind the tokens it issues. Cloud Run runs several
instances, and a client's requests land on any of them, so the state lives in
Postgres: a database of its own on hackbot's Cloud SQL instance, reached the
same way hackbot-api reaches its own.

Everything is encrypted with a key of this service's, since the Google tokens
are users' credentials. FastMCP only does that for its default (local file)
store, not for a store passed to it.
"""

import asyncio
import logging

import asyncpg
from google.cloud.sql.connector import Connector
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.stores.postgresql import PostgreSQLStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from hackbot_mcp.config import Settings

log = logging.getLogger(__name__)

TABLE = "oauth_state"


class CloudSQLStore(PostgreSQLStore):
    """`PostgreSQLStore` connecting through the Cloud SQL Python connector.

    The pool is created on first use, inside the server's event loop, which
    the connector needs.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(table_name=TABLE)
        self._settings = settings

    async def _create_pool(self) -> asyncpg.Pool:
        settings = self._settings
        connector = Connector(loop=asyncio.get_running_loop())
        self._exit_stack.push_async_callback(connector.close_async)

        async def connect(*args, **kwargs) -> asyncpg.Connection:
            return await connector.connect_async(
                settings.cloud_sql_instance,
                "asyncpg",
                user=settings.db_user,
                password=settings.db_pass.get_secret_value(),
                db=settings.db_name,
                ip_type=settings.cloud_sql_ip_type,
            )

        return await asyncpg.create_pool(connect=connect, min_size=1, max_size=5)


async def purge_expired(store: AsyncKeyValue) -> None:
    """Delete entries past their expiry.

    Reads already ignore them; this only keeps the table from growing, so a
    failure is logged rather than raised.
    """
    if not isinstance(store, PostgreSQLStore):
        return
    try:
        await store.setup()
        result = await store._initialized_pool.execute(
            f"DELETE FROM {TABLE} WHERE expires_at < now()"
        )
        log.info("Purged expired OAuth state (%s)", result)
    except Exception:
        log.exception("Failed to purge expired OAuth state")


def build_storage(settings: Settings) -> tuple[AsyncKeyValue, AsyncKeyValue]:
    """The encrypted store to give the OAuth proxy, and the store under it."""
    if settings.cloud_sql_instance:
        base: AsyncKeyValue = CloudSQLStore(settings)
    elif url := settings.database_url.get_secret_value():
        base = PostgreSQLStore(url=url, table_name=TABLE)
    else:
        # Development only (Settings refuses it elsewhere): one process, and
        # every restart signs everyone out.
        log.warning("Keeping OAuth state in memory; for development only")
        base = MemoryStore()

    encrypted = FernetEncryptionWrapper(
        key_value=base,
        source_material=settings.storage_encryption_key.get_secret_value(),
        salt="hackbot-mcp-oauth-state",
        # A key rotation then reads old entries as missing, which signs users
        # out instead of failing their requests.
        raise_on_decryption_error=False,
    )
    return encrypted, base
