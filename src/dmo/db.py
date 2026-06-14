from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.config import settings

_engine: AsyncEngine | None = None
async_session = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        from sqlalchemy.ext.asyncio import create_async_engine

        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            pool_pre_ping=True,
            pool_recycle=3600,
            isolation_level="REPEATABLE_READ",
        )
        global async_session
        async_session = async_sessionmaker(_engine, class_=AsyncSession)
    return _engine


async def get_session(timeout_override: float | None = None) -> AsyncGenerator[AsyncSession, None]:
    """Get a read-optimized database session with query timeout.

    Sets statement_timeout via parameterized set_config() to prevent long-running
    queries from blocking connections. Default timeout is query_timeout_seconds (10s).

    Timeout strategy:
      - Read sessions: 10s (fast queries expected, enforced at DB level)
      - Write sessions: 30s (via get_write_session, bulk ops may be slow)
    """
    if async_session is None:
        get_engine()
    async with async_session() as session:
        timeout_ms = int((timeout_override or settings.query_timeout_seconds) * 1000)
        await session.execute(
            text("SELECT set_config('statement_timeout', :timeout, false)").bindparams(
                timeout=str(timeout_ms)
            )
        )
        yield session


async def get_write_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session(timeout_override=settings.request_timeout_seconds):
        yield session
