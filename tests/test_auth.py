import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.config import settings

WRITE_HEADERS = {"X-API-Key": settings.api_key}


@pytest.mark.asyncio
async def test_write_requires_api_key(client: AsyncClient):
    """Write endpoints return 401 without API key header."""
    resp = await client.post("/entities", json={
        "source": "test",
        "source_id": "auth-test-001",
        "name": "Auth Test",
        "place_type": "poi",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_write_rejects_wrong_api_key(client: AsyncClient):
    """Write endpoints return 401 with incorrect API key."""
    resp = await client.post("/entities", json={
        "source": "test",
        "source_id": "auth-test-002",
        "name": "Auth Test",
        "place_type": "poi",
    }, headers={"X-API-Key": "wrong-key"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_write_succeeds_with_correct_api_key(client: AsyncClient, session: AsyncSession):
    """Write endpoints succeed with correct API key."""
    resp = await client.post("/entities", json={
        "source": "test",
        "source_id": "auth-test-003",
        "name": "Auth Test",
        "place_type": "poi",
    }, headers=WRITE_HEADERS)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_read_without_api_key(client: AsyncClient):
    """Read endpoints work without API key."""
    resp = await client.get("/search?q=test")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_delete_requires_api_key(client: AsyncClient):
    """Delete endpoints return 401 without API key."""
    resp = await client.delete("/test/notfound")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_bulk_upsert_requires_api_key(client: AsyncClient):
    """Bulk upsert returns 401 without API key."""
    resp = await client.post("/entities/bulk", json=[{
        "source": "test",
        "source_id": "auth-test-004",
        "name": "Auth Test",
        "place_type": "poi",
    }])
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_update_requires_api_key(client: AsyncClient):
    """Update endpoints return 401 without API key."""
    resp = await client.put("/test/notfound", json={"name": "Updated"})
    assert resp.status_code == 401
