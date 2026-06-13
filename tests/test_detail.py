from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Entity


@pytest.mark.asyncio
async def test_detail_found(client: AsyncClient, session: AsyncSession):
    entity = Entity(
        id=uuid4(),
        source="rexby",
        source_id="abc123",
        name="Detail POI",
        place_type="restaurant",
        phone="+123456",
    )
    session.add(entity)
    await session.commit()

    resp = await client.get("/rexby/abc123")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Detail POI"
    assert data["source"] == "rexby"
    assert data["phone"] == "+123456"


@pytest.mark.asyncio
async def test_detail_not_found(client: AsyncClient):
    resp = await client.get("/rexby/nonexistent")
    assert resp.status_code == 404
