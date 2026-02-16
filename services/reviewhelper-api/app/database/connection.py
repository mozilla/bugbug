import asyncio
from collections.abc import AsyncGenerator

from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

connector: Connector | None = None
engine = None
async_session_maker = None


async def init_db():
    """Initialize database connection. Call this on app startup."""
    global connector, engine, async_session_maker

    loop = asyncio.get_running_loop()
    connector = Connector(loop=loop)

    async def get_connection():
        return await connector.connect_async(
            settings.cloud_sql_instance,
            "asyncpg",
            user=settings.db_user,
            password=settings.db_pass,
            db=settings.db_name,
            ip_type=IPTypes.PUBLIC,
        )

    engine = create_async_engine(
        "postgresql+asyncpg://",
        async_creator=get_connection,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=2,
        pool_timeout=30,
        pool_recycle=1800,
    )

    async_session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    return engine


async def close_db():
    """Close database connection. Call this on app shutdown."""
    global connector, engine
    if engine:
        await engine.dispose()
    if connector:
        await connector.close_async()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
