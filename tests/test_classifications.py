import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Classification, Entity


@pytest.mark.asyncio
async def test_classifications_empty(client: AsyncClient, session):
    resp = await client.get("/classifications")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_classifications_with_data(client: AsyncClient, session):
    entity = Entity(
        source="dzt",
        source_id="test-class-1",
        name="Test Classification Entity",
        place_type="poi",
        latitude=47.0,
        longitude=8.0,
    )
    session.add(entity)
    await session.flush()

    classification = Classification(
        entity_id=entity.id,
        category="accessibility",
        value_code="wheelchair",
        value_title="Wheelchair accessible",
    )
    session.add(classification)
    await session.commit()

    resp = await client.get("/classifications")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["results"]) == 1
    item = data["results"][0]
    assert item["category"] == "accessibility"
    assert item["value_code"] == "wheelchair"
    assert item["entity"] is not None
    assert item["entity"]["name"] == "Test Classification Entity"


@pytest.mark.asyncio
async def test_classifications_filter_by_entity_id(client: AsyncClient, session):
    entity1 = Entity(
        source="dzt",
        source_id="test-class-2",
        name="Entity 1",
        place_type="poi",
        latitude=47.0,
        longitude=8.0,
    )
    session.add(entity1)
    await session.flush()
    entity1_id = str(entity1.id)

    entity2 = Entity(
        source="dzt",
        source_id="test-class-3",
        name="Entity 2",
        place_type="poi",
        latitude=47.1,
        longitude=8.1,
    )
    session.add(entity2)
    await session.flush()

    c1 = Classification(
        entity_id=entity1.id,
        category="accessibility",
        value_code="wheelchair",
        value_title="Wheelchair accessible",
    )
    session.add(c1)

    c2 = Classification(
        entity_id=entity2.id,
        category="family_friendly",
        value_code="kids",
        value_title="Kids welcome",
    )
    session.add(c2)
    await session.commit()

    resp = await client.get(f"/classifications?entity_id={entity1_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["entity_id"] == entity1_id


@pytest.mark.asyncio
async def test_classifications_filter_by_category(client: AsyncClient, session):
    entity = Entity(
        source="dzt",
        source_id="test-class-4",
        name="Category Filter Entity",
        place_type="poi",
        latitude=47.0,
        longitude=8.0,
    )
    session.add(entity)
    await session.flush()

    c1 = Classification(
        entity_id=entity.id,
        category="accessibility",
        value_code="wheelchair",
        value_title="Wheelchair accessible",
    )
    session.add(c1)

    c2 = Classification(
        entity_id=entity.id,
        category="family_friendly",
        value_code="kids",
        value_title="Kids welcome",
    )
    session.add(c2)
    await session.commit()

    resp = await client.get("/classifications?category=accessibility")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["category"] == "accessibility"


@pytest.mark.asyncio
async def test_classifications_filter_by_value_code(client: AsyncClient, session):
    entity = Entity(
        source="dzt",
        source_id="test-class-5",
        name="Value Code Filter Entity",
        place_type="poi",
        latitude=47.0,
        longitude=8.0,
    )
    session.add(entity)
    await session.flush()

    c1 = Classification(
        entity_id=entity.id,
        category="accessibility",
        value_code="wheelchair",
        value_title="Wheelchair accessible",
    )
    session.add(c1)

    c2 = Classification(
        entity_id=entity.id,
        category="accessibility",
        value_code="elevator",
        value_title="Elevator access",
    )
    session.add(c2)
    await session.commit()

    resp = await client.get("/classifications?value_code=wheelchair")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["value_code"] == "wheelchair"


@pytest.mark.asyncio
async def test_classifications_categories_empty(client: AsyncClient, session):
    resp = await client.get("/classifications/categories")
    assert resp.status_code == 200
    data = resp.json()
    assert data == []


@pytest.mark.asyncio
async def test_classifications_pagination(client: AsyncClient, session: AsyncSession):
    entity = Entity(
        source="dzt",
        source_id="test-class-pag",
        name="Pagination Entity",
        place_type="poi",
        latitude=47.0,
        longitude=8.0,
    )
    session.add(entity)
    await session.flush()
    eid = str(entity.id)

    for code, title in [("wheelchair", "Wheelchair"), ("elevator", "Elevator"), ("ramp", "Ramp"), ("audio", "Audio"), ("braille", "Braille")]:
        c = Classification(entity_id=entity.id, category="accessibility", value_code=code, value_title=title)
        session.add(c)
    await session.commit()

    resp = await client.get(f"/classifications?entity_id={eid}&page_size=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["results"]) == 2
    assert data["has_more"] is True
    assert data["next_cursor"] is not None


@pytest.mark.asyncio
async def test_classifications_cursor_pagination(client: AsyncClient, session: AsyncSession):
    entity = Entity(
        source="dzt",
        source_id="test-class-cursor",
        name="Cursor Entity",
        place_type="poi",
        latitude=47.0,
        longitude=8.0,
    )
    session.add(entity)
    await session.flush()
    eid = str(entity.id)

    for code, title in [("wheelchair", "Wheelchair"), ("elevator", "Elevator"), ("ramp", "Ramp"), ("audio", "Audio"), ("braille", "Braille")]:
        c = Classification(entity_id=entity.id, category="accessibility", value_code=code, value_title=title)
        session.add(c)
    await session.commit()

    resp1 = await client.get(f"/classifications?entity_id={eid}&page_size=2")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["has_more"] is True
    cursor = data1["next_cursor"]

    resp2 = await client.get(f"/classifications?entity_id={eid}&page_size=2&cursor={cursor}")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["results"]) == 2
    assert data1["results"][-1]["id"] != data2["results"][0]["id"]


@pytest.mark.asyncio
async def test_classifications_categories_with_data(client: AsyncClient, session):
    entity = Entity(
        source="dzt",
        source_id="test-class-6",
        name="Categories Entity",
        place_type="poi",
        latitude=47.0,
        longitude=8.0,
    )
    session.add(entity)
    await session.flush()

    c1 = Classification(
        entity_id=entity.id,
        category="accessibility",
        value_code="wheelchair",
        value_title="Wheelchair accessible",
    )
    session.add(c1)

    c2 = Classification(
        entity_id=entity.id,
        category="family_friendly",
        value_code="kids",
        value_title="Kids welcome",
    )
    session.add(c2)

    c3 = Classification(
        entity_id=entity.id,
        category="accessibility",
        value_code="elevator",
        value_title="Elevator access",
    )
    session.add(c3)
    await session.commit()

    resp = await client.get("/classifications/categories")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert "accessibility" in data
    assert "family_friendly" in data
