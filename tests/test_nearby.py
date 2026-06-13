from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Entity


def _make_entity(source: str = "test", name: str = "Test POI", place_type: str = "poi", lat: float = 46.95, lon: float = 7.45):
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
        text("UPDATE entities SET location = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) WHERE id = :id").bindparams(
            lat=entity.latitude, lon=entity.longitude, id=entity.id
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_nearby_empty(client: AsyncClient):
    resp = await client.get("/nearby?lat=46.95&lon=7.45&radius_km=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_nearby_with_data(client: AsyncClient, session: AsyncSession):
    entity = _make_entity(name="Close POI", lat=46.951, lon=7.451)
    await _insert_with_location(session, entity)

    resp = await client.get("/nearby?lat=46.95&lon=7.45&radius_km=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["name"] == "Close POI"
    assert data["results"][0]["distance_km"] is not None


@pytest.mark.asyncio
async def test_nearby_outside_radius(client: AsyncClient, session: AsyncSession):
    entity = _make_entity(name="Far POI", lat=47.5, lon=8.5)
    await _insert_with_location(session, entity)

    resp = await client.get("/nearby?lat=46.95&lon=7.45&radius_km=10")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_nearby_filter_source(client: AsyncClient, session: AsyncSession):
    e1 = _make_entity(source="rexby", name="Rexby POI", lat=46.951, lon=7.451)
    e2 = _make_entity(source="dzt", name="DZT POI", lat=46.952, lon=7.452)
    await _insert_with_location(session, e1)
    await _insert_with_location(session, e2)

    resp = await client.get("/nearby?lat=46.95&lon=7.45&radius_km=10&source=rexby")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["source"] == "rexby"

    resp2 = await client.get("/nearby?lat=46.95&lon=7.45&radius_km=10&source=dzt")
    assert resp2.json()["total"] == 1
    assert resp2.json()["results"][0]["source"] == "dzt"


@pytest.mark.asyncio
async def test_nearby_ordered_by_distance(client: AsyncClient, session: AsyncSession):
    e1 = _make_entity(name="Far POI", lat=46.96, lon=7.46)
    e2 = _make_entity(name="Close POI", lat=46.951, lon=7.451)
    await _insert_with_location(session, e1)
    await _insert_with_location(session, e2)

    resp = await client.get("/nearby?lat=46.95&lon=7.45&radius_km=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["results"][0]["name"] == "Close POI"
    assert data["results"][1]["name"] == "Far POI"
    assert data["results"][0]["distance_km"] < data["results"][1]["distance_km"]


@pytest.mark.asyncio
async def test_nearby_pagination(client: AsyncClient, session: AsyncSession):
    for i in range(5):
        entity = _make_entity(name=f"POI {i}", lat=46.95 + i * 0.001, lon=7.45 + i * 0.001)
        await _insert_with_location(session, entity)

    resp = await client.get("/nearby?lat=46.95&lon=7.45&radius_km=10&page_size=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["results"]) == 2
    assert data["has_more"] is True
    assert data["next_cursor"] is not None


@pytest.mark.asyncio
async def test_nearby_cursor_pagination(client: AsyncClient, session: AsyncSession):
    for i in range(5):
        entity = _make_entity(name=f"POI {i}", lat=46.95 + i * 0.001, lon=7.45 + i * 0.001)
        await _insert_with_location(session, entity)

    resp1 = await client.get("/nearby?lat=46.95&lon=7.45&radius_km=10&page_size=2")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["has_more"] is True
    cursor = data1["next_cursor"]

    resp2 = await client.get(f"/nearby?lat=46.95&lon=7.45&radius_km=10&page_size=2&cursor={cursor}")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["results"]) == 2
    assert data1["results"][-1]["id"] != data2["results"][0]["id"]
