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


@pytest.mark.asyncio
async def test_detail_includes_unified_fields(client: AsyncClient, session: AsyncSession):
    """Test that detail endpoint returns unified_category and unified_subcategory."""
    entity = Entity(
        id=uuid4(),
        source="rexby",
        source_id="unified-detail-test",
        name="Unified Detail POI",
        place_type="restaurant",
        unified_category="food_drink",
        unified_subcategory="restaurant",
    )
    session.add(entity)
    await session.commit()

    resp = await client.get("/rexby/unified-detail-test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["unified_category"] == "food_drink"
    assert data["unified_subcategory"] == "restaurant"


@pytest.mark.asyncio
async def test_detail_unified_fields_null_when_unset(client: AsyncClient, session: AsyncSession):
    """Test that detail returns null for unified fields when entity has none set."""
    entity = Entity(
        id=uuid4(),
        source="rexby",
        source_id="no-unified-detail",
        name="No Unified POI",
        place_type="attraction",
    )
    session.add(entity)
    await session.commit()

    resp = await client.get("/rexby/no-unified-detail")
    assert resp.status_code == 200
    data = resp.json()
    assert data["unified_category"] is None
    assert data["unified_subcategory"] is None
