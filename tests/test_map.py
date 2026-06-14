from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Entity


def _make_entity(
    source: str = "test",
    name: str = "Test POI",
    place_type: str = "poi",
    lat: float = 46.95,
    lon: float = 7.45,
):
    return Entity(
        id=uuid4(),
        source=source,
        source_id=name.replace(" ", "-").lower(),
        name=name,
        place_type=place_type,
        latitude=lat,
        longitude=lon,
    )


async def _insert_with_location(session: AsyncSession, entity: Entity):
    """Insert entity with PostGIS location set via raw SQL."""
    session.add(entity)
    await session.commit()
    await session.refresh(entity)
    await session.execute(
        text(
            "UPDATE entities SET location = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) WHERE id = :id"
        ).bindparams(lat=entity.latitude, lon=entity.longitude, id=entity.id)
    )
    await session.commit()


@pytest.mark.asyncio
async def test_map_empty(client: AsyncClient):
    resp = await client.get("/map?bbox=7.4,46.9,7.5,47.0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_map_with_data(client: AsyncClient, session: AsyncSession):
    entity = _make_entity(name="In Box POI", lat=46.95, lon=7.45)
    await _insert_with_location(session, entity)

    resp = await client.get("/map?bbox=7.4,46.9,7.5,47.0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["name"] == "In Box POI"


@pytest.mark.asyncio
async def test_map_outside_bbox(client: AsyncClient, session: AsyncSession):
    entity = _make_entity(name="Outside POI", lat=47.5, lon=8.5)
    await _insert_with_location(session, entity)

    resp = await client.get("/map?bbox=7.4,46.9,7.5,47.0")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_map_filter_place_type(client: AsyncClient, session: AsyncSession):
    e1 = _make_entity(name="Hike POI", place_type="hike", lat=46.95, lon=7.45)
    e2 = _make_entity(name="Restaurant POI", place_type="restaurant", lat=46.96, lon=7.46)
    await _insert_with_location(session, e1)
    await _insert_with_location(session, e2)

    resp = await client.get("/map?bbox=7.4,46.9,7.5,47.0&place_type=hike")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["place_type"] == "hike"


@pytest.mark.asyncio
async def test_map_invalid_bbox(client: AsyncClient):
    resp = await client.get("/map?bbox=7.4,46.9")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_map_invalid_bbox_values(client: AsyncClient):
    resp = await client.get("/map?bbox=abc,def,ghi,jkl")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_map_pagination(client: AsyncClient, session: AsyncSession):
    for i in range(5):
        entity = _make_entity(name=f"POI {i}", lat=46.95 + i * 0.005, lon=7.45 + i * 0.005)
        await _insert_with_location(session, entity)

    resp = await client.get("/map?bbox=7.4,46.9,7.5,47.0&page_size=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["results"]) == 2
    assert data["has_more"] is True
    assert data["next_cursor"] is not None


@pytest.mark.asyncio
async def test_map_cursor_pagination(client: AsyncClient, session: AsyncSession):
    for i in range(5):
        entity = _make_entity(name=f"POI {i}", lat=46.95 + i * 0.005, lon=7.45 + i * 0.005)
        await _insert_with_location(session, entity)

    resp1 = await client.get("/map?bbox=7.4,46.9,7.5,47.0&page_size=2")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["has_more"] is True
    cursor = data1["next_cursor"]

    resp2 = await client.get(f"/map?bbox=7.4,46.9,7.5,47.0&page_size=2&cursor={cursor}")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["results"]) == 2
    assert data1["results"][-1]["id"] != data2["results"][0]["id"]
