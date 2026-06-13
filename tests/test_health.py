from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ok", "degraded")
    assert "components" in data
    assert "database" in data["components"]
    assert "redis" in data["components"]


@pytest.mark.asyncio
async def test_health_db_down(client: AsyncClient, session: AsyncSession):
    await session.close()
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["components"]["database"] in ("up", "down")


@pytest.mark.asyncio
async def test_health_db_timeout(client: AsyncClient):
    async def mock_wait_for(coro, timeout=None):
        raise TimeoutError()

    with patch("dmo.api.health.asyncio.wait_for", side_effect=mock_wait_for):
        resp = await client.get("/health")

    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["components"]["database"] == "timeout"
    assert data["components"]["redis"] == "timeout"


@pytest.mark.asyncio
async def test_health_redis_timeout(client: AsyncClient):
    call_count = 0

    async def mock_wait_for(coro, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise TimeoutError()
        return await coro

    with patch("dmo.api.health.asyncio.wait_for", side_effect=mock_wait_for):
        resp = await client.get("/health")

    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["components"]["redis"] == "timeout"
