import os

# Override env vars for local testing before any dmo imports.
# .env has Docker hostnames (db, redis); tests need localhost.
# If TEST_DB_URL is set (e.g., staging container), use it instead of localhost.
test_db_url = os.environ.get(
    "TEST_DB_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/dmo"
)
os.environ["DATABASE_URL"] = test_db_url
os.environ["DATABASE_URL_SYNC"] = test_db_url.replace("asyncpg", "psycopg2")
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/0")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.main import app
from dmo.services.cache import cache_delete_pattern as _orig_cache_delete_pattern

# Save originals before any patching
from dmo.services.cache import cache_get as _orig_cache_get
from dmo.services.cache import cache_get_or_set as _orig_cache_get_or_set
from dmo.services.cache import cache_set as _orig_cache_set
from dmo.services.source_filter import invalidate_cache as _invalidate_source_cache

TEST_DB_URL = os.environ.get(
    "TEST_DB_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/dmo"
)


@pytest.fixture(autouse=True)
def _disable_cache(request):
    """Disable caching during tests."""
    import dmo.api.router as router_module
    import dmo.services.cache as cache_module
    import dmo.services.write as write_module

    async def _no_op_get(*args, **kwargs):
        return None

    async def _no_op_set(*args, **kwargs):
        pass

    async def _no_op_get_or_set(*args, fetch_fn=None, **kwargs):
        if fetch_fn:
            return await fetch_fn(), "MISS"
        return None, "MISS"

    async def _no_op_delete_pattern(*args, **kwargs):
        pass

    # Don't patch cache_get_or_set for stampede tests
    is_stampede = "test_cache_stampede" in (
        request.node.module.__name__ if request.node.module else ""
    )

    # Clear disabled sources cache at start of each test
    _invalidate_source_cache()

    # Don't patch cache_delete_pattern for cache tests
    is_cache_test = "test_cache" in (request.node.module.__name__ if request.node.module else "")

    cache_module.cache_get = _no_op_get
    cache_module.cache_set = _no_op_set
    if not is_cache_test:
        cache_module.cache_delete_pattern = _no_op_delete_pattern
        write_module.cache_delete_pattern = _no_op_delete_pattern
    if not is_stampede:
        cache_module.cache_get_or_set = _no_op_get_or_set
    router_module.cache_get = _no_op_get
    router_module.cache_set = _no_op_set
    if not is_stampede:
        router_module.cache_get_or_set = _no_op_get_or_set

    yield

    # Restore originals
    cache_module.cache_get = _orig_cache_get
    cache_module.cache_set = _orig_cache_set
    cache_module.cache_get_or_set = _orig_cache_get_or_set
    if not is_cache_test:
        cache_module.cache_delete_pattern = _orig_cache_delete_pattern
        write_module.cache_delete_pattern = _orig_cache_delete_pattern
    router_module.cache_get = _orig_cache_get
    router_module.cache_set = _orig_cache_set
    router_module.cache_get_or_set = _orig_cache_get_or_set
    _invalidate_source_cache()


@pytest.fixture(scope="session")
async def engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(SQLModel.metadata.create_all)
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
        await s.exec(
            text(
                "DELETE FROM data_sources WHERE source NOT IN (SELECT DISTINCT source FROM entities WHERE is_active = TRUE)"
            )
        )
        await s.commit()

        yield s

        # Clean up test data at the end
        await s.exec(text("DELETE FROM routes"))
        await s.exec(text("DELETE FROM classifications"))
        await s.exec(text("DELETE FROM media"))
        await s.exec(text("DELETE FROM entities"))
        await s.exec(
            text(
                "DELETE FROM data_sources WHERE source NOT IN (SELECT DISTINCT source FROM entities WHERE is_active = TRUE)"
            )
        )
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
