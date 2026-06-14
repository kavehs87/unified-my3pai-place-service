import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.config import settings

WRITE_HEADERS = {"X-API-Key": settings.api_key}


def _make_entity_data(**overrides):
    data = {
        "source": "test",
        "source_id": "test-001",
        "name": "Test Entity",
        "place_type": "poi",
        "latitude": 46.95,
        "longitude": 7.45,
        "country": "CH",
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_create_entity(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data()
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 201
    result = resp.json()
    assert result["name"] == "Test Entity"
    assert result["source"] == "test"
    assert result["source_id"] == "test-001"
    assert result["place_type"] == "poi"


@pytest.mark.asyncio
async def test_create_entity_duplicate(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data()
    resp1 = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp1.status_code == 201

    resp2 = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_create_entity_with_attributes(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data(attributes={"distance_km": "5.2", "rating": "4.5"})
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 201
    result = resp.json()
    assert result["attributes"]["distance_km"] == "5.2"


@pytest.mark.asyncio
async def test_update_entity(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data()
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 201

    update_data = {"name": "Updated Name", "summary": "New summary"}
    resp = await client.put("/test/test-001", json=update_data, headers=WRITE_HEADERS)
    assert resp.status_code == 200
    result = resp.json()
    assert result["name"] == "Updated Name"


@pytest.mark.asyncio
async def test_update_entity_not_found(client: AsyncClient, session: AsyncSession):
    update_data = {"name": "Updated Name"}
    resp = await client.put("/test/notfound", json=update_data, headers=WRITE_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_entity(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data()
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 201

    resp = await client.delete("/test/test-001", headers=WRITE_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


@pytest.mark.asyncio
async def test_delete_entity_not_found(client: AsyncClient, session: AsyncSession):
    resp = await client.delete("/test/notfound", headers=WRITE_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_bulk_upsert(client: AsyncClient, session: AsyncSession):
    entities = [
        _make_entity_data(source_id="bulk-001", name="Bulk 1"),
        _make_entity_data(source_id="bulk-002", name="Bulk 2"),
    ]
    resp = await client.post("/entities/bulk", json=entities, headers=WRITE_HEADERS)
    assert resp.status_code == 201
    results = resp.json()
    assert len(results) == 2
    names = {r["name"] for r in results}
    assert "Bulk 1" in names
    assert "Bulk 2" in names


@pytest.mark.asyncio
async def test_bulk_upsert_update_existing(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data(source_id="upsert-001", name="Original")
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 201

    entities = [_make_entity_data(source_id="upsert-001", name="Updated")]
    resp = await client.post("/entities/bulk", json=entities, headers=WRITE_HEADERS)
    assert resp.status_code == 201
    results = resp.json()
    assert len(results) == 1
    assert results[0]["name"] == "Updated"


@pytest.mark.asyncio
async def test_create_media(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data()
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 201
    entity_id = resp.json()["id"]

    media_data = {
        "entity_id": entity_id,
        "media_type": "image",
        "url": "https://example.com/photo.jpg",
        "name": "Test Photo",
    }
    resp = await client.post("/media", json=media_data, headers=WRITE_HEADERS)
    assert resp.status_code == 201
    result = resp.json()
    assert result["entity_id"] == entity_id


@pytest.mark.asyncio
async def test_create_media_entity_not_found(client: AsyncClient, session: AsyncSession):
    import uuid

    media_data = {
        "entity_id": str(uuid.uuid4()),
        "media_type": "image",
        "url": "https://example.com/photo.jpg",
    }
    resp = await client.post("/media", json=media_data, headers=WRITE_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_media(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data()
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 201
    entity_id = resp.json()["id"]

    media_data = {
        "entity_id": entity_id,
        "media_type": "image",
        "url": "https://example.com/photo.jpg",
    }
    resp = await client.post("/media", json=media_data, headers=WRITE_HEADERS)
    assert resp.status_code == 201
    media_id = resp.json()["id"]

    resp = await client.delete(f"/media/{media_id}", headers=WRITE_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


@pytest.mark.asyncio
async def test_delete_media_not_found(client: AsyncClient, session: AsyncSession):
    resp = await client.delete("/media/99999", headers=WRITE_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_classification(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data()
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 201
    entity_id = resp.json()["id"]

    classif_data = {
        "entity_id": entity_id,
        "category": "tourism",
        "value_code": "museum",
        "value_title": "Museum",
    }
    resp = await client.post("/classifications", json=classif_data, headers=WRITE_HEADERS)
    assert resp.status_code == 201
    result = resp.json()
    assert result["entity_id"] == entity_id


@pytest.mark.asyncio
async def test_create_classification_entity_not_found(client: AsyncClient, session: AsyncSession):
    import uuid

    classif_data = {
        "entity_id": str(uuid.uuid4()),
        "category": "tourism",
        "value_code": "museum",
    }
    resp = await client.post("/classifications", json=classif_data, headers=WRITE_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_bulk_upsert_location_accuracy(client: AsyncClient, session: AsyncSession):
    """Verify bulk upsert sets geography column correctly for diverse coordinates."""
    entities = [
        _make_entity_data(source_id="loc-001", name="Zurich", latitude=47.3769, longitude=8.5417),
        _make_entity_data(
            source_id="loc-002", name="New York", latitude=40.7128, longitude=-74.0060
        ),
        _make_entity_data(
            source_id="loc-003", name="Sydney", latitude=-33.8688, longitude=151.2093
        ),
        _make_entity_data(source_id="loc-004", name="Equator", latitude=0.0, longitude=0.0),
    ]
    resp = await client.post("/entities/bulk", json=entities, headers=WRITE_HEADERS)
    assert resp.status_code == 201
    results = resp.json()
    assert len(results) == 4

    from sqlmodel import text as sql_text

    for e in entities:
        row = await session.exec(
            sql_text(
                "SELECT ST_AsText(location) FROM entities WHERE source = :src AND source_id = :sid"
            ).bindparams(src=e["source"], sid=e["source_id"])
        )
        loc = row.scalar_one_or_none()
        assert loc is not None, f"Location NULL for {e['source_id']}"
        # ST_AsText outputs "POINT(lon lat)" — parse and compare as floats
        coords = loc.replace("POINT(", "").replace(")", "").split()
        assert float(coords[0]) == pytest.approx(e["longitude"], abs=1e-4), (
            f"Wrong longitude for {e['source_id']}: {loc}"
        )
        assert float(coords[1]) == pytest.approx(e["latitude"], abs=1e-4), (
            f"Wrong latitude for {e['source_id']}: {loc}"
        )


@pytest.mark.asyncio
async def test_bulk_upsert_location_update_existing(client: AsyncClient, session: AsyncSession):
    """Verify bulk upsert updates location on existing entities."""
    from sqlmodel import text as sql_text

    data = _make_entity_data(
        source_id="loc-upd-001", name="Original", latitude=46.95, longitude=7.45
    )
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 201

    row = await session.exec(
        sql_text(
            "SELECT ST_AsText(location) FROM entities WHERE source = 'test' AND source_id = 'loc-upd-001'"
        )
    )
    assert "7.45" in row.scalar_one_or_none()

    entities = [
        _make_entity_data(
            source_id="loc-upd-001", name="Updated", latitude=47.3769, longitude=8.5417
        )
    ]
    resp = await client.post("/entities/bulk", json=entities, headers=WRITE_HEADERS)
    assert resp.status_code == 201

    row = await session.exec(
        sql_text(
            "SELECT ST_AsText(location) FROM entities WHERE source = 'test' AND source_id = 'loc-upd-001'"
        )
    )
    loc = row.scalar_one_or_none()
    assert "8.5417" in loc and "47.3769" in loc, f"Location not updated: {loc}"


@pytest.mark.asyncio
async def test_set_locations_batch_empty(session: AsyncSession):
    """Verify _set_locations_batch handles empty list without error."""
    from dmo.services.write import _set_locations_batch

    await _set_locations_batch(session, [])


@pytest.mark.asyncio
async def test_set_locations_batch_edge_values(session: AsyncSession):
    """Verify _set_locations_batch handles edge case coordinates."""
    from dmo.models.database import Entity
    from dmo.services.write import _set_locations_batch

    entity = Entity(source="test", source_id="edge-001", name="Edge", place_type="poi")
    session.add(entity)
    await session.flush()

    await _set_locations_batch(session, [(entity.id, -180.0, -90.0)])
    await session.commit()
    await session.refresh(entity)
    assert entity.location is not None


@pytest.mark.asyncio
async def test_write_invalidates_cache(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data(source_id="cache-test", name="Cache Test")
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 201

    resp = await client.get("/search?q=Cache+Test")
    assert resp.status_code == 200
    resp.json()

    update_data = {"name": "Cache Test Updated"}
    resp = await client.put("/test/cache-test", json=update_data, headers=WRITE_HEADERS)
    assert resp.status_code == 200

    resp = await client.get("/search?q=Cache+Test+Updated")
    assert resp.status_code == 200
    new_results = resp.json()
    assert any("Cache Test Updated" in r.get("name", "") for r in new_results.get("results", []))


@pytest.mark.asyncio
async def test_bulk_upsert_concurrent_no_conflict(engine):
    """Two concurrent bulk upserts with overlapping source_ids should both succeed (serialized by advisory lock)."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel import text as sql_text
    from sqlmodel.ext.asyncio.session import AsyncSession

    from dmo.models.schemas import EntityCreate
    from dmo.services.write import bulk_upsert

    async_session = async_sessionmaker(engine, class_=AsyncSession)

    batch_a = [
        EntityCreate(
            source="test",
            source_id="concurrent-shared",
            name="Batch A",
            place_type="poi",
            latitude=46.95,
            longitude=7.45,
            country="CH",
        )
    ]
    batch_b = [
        EntityCreate(
            source="test",
            source_id="concurrent-shared",
            name="Batch B",
            place_type="poi",
            latitude=46.95,
            longitude=7.45,
            country="CH",
        )
    ]

    async def upsert_a():
        async with async_session() as s:
            await s.exec(sql_text("DELETE FROM routes"))
            await s.exec(sql_text("DELETE FROM classifications"))
            await s.exec(sql_text("DELETE FROM media"))
            await s.exec(sql_text("DELETE FROM entities"))
            await s.commit()
            return await bulk_upsert(s, batch_a)

    async def upsert_b():
        async with async_session() as s:
            return await bulk_upsert(s, batch_b)

    result_a, result_b = await asyncio.gather(upsert_a(), upsert_b())
    assert len(result_a) == 1
    assert len(result_b) == 1


@pytest.mark.asyncio
async def test_bulk_upsert_large_batch_uses_full_cache_invalidation(engine):
    """Large bulk upsert (>=21 entities) should use invalidate_all_caches for O(1) invalidation."""
    from unittest.mock import patch

    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel import text as sql_text
    from sqlmodel.ext.asyncio.session import AsyncSession

    from dmo.models.schemas import EntityCreate
    from dmo.services import write as write_module
    from dmo.services.write import bulk_upsert

    async_session = async_sessionmaker(engine, class_=AsyncSession)

    entities = []
    for i in range(25):
        entities.append(
            EntityCreate(
                source="test_bulk_cache",
                source_id=f"cache_test_{i}",
                name=f"Bulk Cache Entity {i}",
                place_type="point_of_interest",
            )
        )

    invalidation_calls = []

    async def fake_invalidate_all():
        invalidation_calls.append("all")

    async def fake_invalidate_entity(eid):
        invalidation_calls.append(("entity", eid))

    with (
        patch.object(write_module, "invalidate_all_caches", fake_invalidate_all),
        patch.object(write_module, "invalidate_entity_caches", fake_invalidate_entity),
    ):
        async with async_session() as session:
            await session.exec(sql_text("DELETE FROM routes"))
            await session.exec(sql_text("DELETE FROM classifications"))
            await session.exec(sql_text("DELETE FROM media"))
            await session.exec(sql_text("DELETE FROM entities"))
            await session.commit()
            await bulk_upsert(session, entities)

    assert len(invalidation_calls) == 1
    assert invalidation_calls[0] == "all"


@pytest.mark.asyncio
async def test_bulk_upsert_small_batch_uses_per_entity_invalidation(engine):
    """Small bulk upsert (<21 entities) should use per-entity cache invalidation."""
    from unittest.mock import patch

    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel import text as sql_text
    from sqlmodel.ext.asyncio.session import AsyncSession

    from dmo.models.schemas import EntityCreate
    from dmo.services import write as write_module
    from dmo.services.write import bulk_upsert

    async_session = async_sessionmaker(engine, class_=AsyncSession)

    entities = []
    for i in range(5):
        entities.append(
            EntityCreate(
                source="test_bulk_small",
                source_id=f"small_cache_{i}",
                name=f"Small Bulk Entity {i}",
                place_type="point_of_interest",
            )
        )

    invalidation_calls = []

    async def fake_invalidate_all():
        invalidation_calls.append("all")

    async def fake_invalidate_entity(eid):
        invalidation_calls.append(("entity", eid))

    with (
        patch.object(write_module, "invalidate_all_caches", fake_invalidate_all),
        patch.object(write_module, "invalidate_entity_caches", fake_invalidate_entity),
    ):
        async with async_session() as session:
            await session.exec(sql_text("DELETE FROM routes"))
            await session.exec(sql_text("DELETE FROM classifications"))
            await session.exec(sql_text("DELETE FROM media"))
            await session.exec(sql_text("DELETE FROM entities"))
            await session.commit()
            await bulk_upsert(session, entities)

    assert len(invalidation_calls) == 5
    assert all(isinstance(c, tuple) and c[0] == "entity" for c in invalidation_calls)
