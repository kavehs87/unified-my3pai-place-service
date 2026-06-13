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
