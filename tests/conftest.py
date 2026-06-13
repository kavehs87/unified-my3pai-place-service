import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.main import app

TEST_DB_URL = os.environ.get("TEST_DB_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/dmo")


@pytest.fixture(autouse=True)
def _disable_cache():
    """Disable caching during tests."""
    import dmo.api.router as router_module
    import dmo.services.cache as cache_module

    async def _no_op_get(*args, **kwargs):
        return None

    async def _no_op_set(*args, **kwargs):
        pass

    cache_module.cache_get = _no_op_get
    cache_module.cache_set = _no_op_set
    router_module.cache_get = _no_op_get
    router_module.cache_set = _no_op_set

    yield

    # Restore original functions (not needed for tests, but good practice)
    from dmo.services.cache import cache_get, cache_set
    cache_module.cache_get = cache_get
    cache_module.cache_set = cache_set
    router_module.cache_get = cache_get
    router_module.cache_set = cache_set


@pytest.fixture(scope="session")
async def engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        # Add is_active columns if they don't exist (for existing test DB)
        await conn.execute(text("ALTER TABLE media ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"))
        await conn.execute(text("ALTER TABLE classifications ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"))
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine) -> AsyncSession:
    async_session = async_sessionmaker(engine, class_=AsyncSession)
    async with async_session() as s:
        # Clean up test data at the start
        await s.exec(text("DELETE FROM routes"))
        await s.exec(text("DELETE FROM classifications"))
        await s.exec(text("DELETE FROM media"))
        await s.exec(text("DELETE FROM entities"))
        await s.commit()

        yield s

        # Clean up test data at the end
        await s.exec(text("DELETE FROM routes"))
        await s.exec(text("DELETE FROM classifications"))
        await s.exec(text("DELETE FROM media"))
        await s.exec(text("DELETE FROM entities"))
        await s.commit()


@pytest.fixture
async def client(session: AsyncSession) -> AsyncClient:
    from dmo.db import get_session

    async def _override():
        yield session

    app.dependency_overrides[get_session] = _override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
