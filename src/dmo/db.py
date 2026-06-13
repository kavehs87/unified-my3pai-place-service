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
        )
        global async_session
        async_session = async_sessionmaker(_engine, class_=AsyncSession)
    return _engine


async def get_session(timeout_override: float | None = None) -> AsyncGenerator[AsyncSession, None]:
    if async_session is None:
        get_engine()
    async with async_session() as session:
        timeout_ms = int((timeout_override or settings.query_timeout_seconds) * 1000)
        await session.execute(text(f"SET statement_timeout = '{timeout_ms}'"))
        yield session


async def get_write_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session(timeout_override=settings.request_timeout_seconds):
        yield session
