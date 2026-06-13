import pytest


@pytest.mark.asyncio
async def test_engine_isolation_level():
    """Verify engine is created with REPEATABLE_READ isolation level."""
    from sqlalchemy import text

    from dmo.db import get_engine, get_session

    get_engine()

    async for sess in get_session():
        result = await sess.execute(text("SHOW transaction_isolation"))
        isolation = result.scalar()
        assert isolation == "repeatable read", f"Expected repeatable read, got {isolation}"
        break


@pytest.mark.asyncio
async def test_write_operation_isolation_level():
    """Verify write session uses REPEATABLE_READ isolation level."""
    from sqlalchemy import text

    from dmo.db import get_engine, get_write_session

    get_engine()

    async for sess in get_write_session():
        result = await sess.execute(text("SHOW transaction_isolation"))
        isolation = result.scalar()
        assert isolation == "repeatable read", f"Expected repeatable read, got {isolation}"
        break
