from collections.abc import AsyncGenerator

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


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if async_session is None:
        get_engine()
    async with async_session() as session:
        yield session
