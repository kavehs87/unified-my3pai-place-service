from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Entity


@pytest.mark.asyncio
async def test_search_empty(client: AsyncClient):
    resp = await client.get("/search")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_search_with_data(client: AsyncClient, session: AsyncSession):
    entity = Entity(
        id=uuid4(),
        source="test",
        source_id="1",
        name="Test POI",
        place_type="poi",
        country="CH",
    )
    session.add(entity)
    await session.commit()

    resp = await client.get("/search?q=Test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["name"] == "Test POI"


@pytest.mark.asyncio
async def test_search_filter_by_source(client: AsyncClient, session: AsyncSession):
    entity = Entity(
        id=uuid4(),
        source="rexby",
        source_id="1",
        name="Rexby POI",
        place_type="hike",
    )
    session.add(entity)
    await session.commit()

    resp = await client.get("/search?source=rexby")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1

    resp2 = await client.get("/search?source=dzt")
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 0


@pytest.mark.asyncio
async def test_search_filter_by_place_type(client: AsyncClient, session: AsyncSession):
    entity = Entity(
        id=uuid4(),
        source="test",
        source_id="1",
        name="Hike Trail",
        place_type="hike",
    )
    session.add(entity)
    await session.commit()

    resp = await client.get("/search?place_type=hike")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp2 = await client.get("/search?place_type=restaurant")
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 0


@pytest.mark.asyncio
async def test_search_pagination(client: AsyncClient, session: AsyncSession):
    for i in range(5):
        entity = Entity(
            id=uuid4(),
            source="test",
            source_id=str(i),
            name=f"POI {i}",
            place_type="poi",
        )
        session.add(entity)
    await session.commit()

    resp = await client.get("/search?page_size=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["results"]) == 2
    assert data["has_more"] is True
    assert data["next_cursor"] is not None
    assert "page" not in data


@pytest.mark.asyncio
async def test_search_cursor_pagination(client: AsyncClient, session: AsyncSession):
    for i in range(5):
        entity = Entity(
            id=uuid4(),
            source="test",
            source_id=str(i),
            name=f"POI {i}",
            place_type="poi",
        )
        session.add(entity)
    await session.commit()

    resp1 = await client.get("/search?page_size=2")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["has_more"] is True
    cursor = data1["next_cursor"]

    resp2 = await client.get(f"/search?page_size=2&cursor={cursor}")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["results"]) == 2
    assert data1["results"][-1]["id"] != data2["results"][0]["id"]


@pytest.mark.asyncio
async def test_search_fulltext_flag(client: AsyncClient, session: AsyncSession):
    """Test that fulltext flag enables summary search.

    Default (fulltext=False) searches name only.
    fulltext=True searches name + summary.
    """
    entity_name = Entity(
        id=uuid4(),
        source="test",
        source_id="ft-1",
        name="Mountain Lodge",
        place_type="hotel",
        country="CH",
    )
    entity_summary = Entity(
        id=uuid4(),
        source="test",
        source_id="ft-2",
        name="Alpine Hotel",
        summary="Cozy hotel near Mountain Lodge in the Alps",
        place_type="hotel",
        country="CH",
    )
    session.add(entity_name)
    session.add(entity_summary)
    await session.commit()

    # Default: name-only search finds "Mountain Lodge"
    resp = await client.get("/search?q=Mountain+Lodge")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["name"] == "Mountain Lodge"

    # fulltext=True: summary search finds "Alpine Hotel" (summary mentions "Mountain Lodge")
    resp2 = await client.get("/search?q=Mountain+Lodge&fulltext=true")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total"] == 2
