import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession


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
    resp = await client.post("/entities", json=data)
    assert resp.status_code == 201
    result = resp.json()
    assert result["name"] == "Test Entity"
    assert result["source"] == "test"
    assert result["source_id"] == "test-001"
    assert result["place_type"] == "poi"


@pytest.mark.asyncio
async def test_create_entity_duplicate(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data()
    resp1 = await client.post("/entities", json=data)
    assert resp1.status_code == 201

    resp2 = await client.post("/entities", json=data)
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_create_entity_with_attributes(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data(attributes={"distance_km": "5.2", "rating": "4.5"})
    resp = await client.post("/entities", json=data)
    assert resp.status_code == 201
    result = resp.json()
    assert result["attributes"]["distance_km"] == "5.2"


@pytest.mark.asyncio
async def test_update_entity(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data()
    resp = await client.post("/entities", json=data)
    assert resp.status_code == 201

    update_data = {"name": "Updated Name", "summary": "New summary"}
    resp = await client.put("/test/test-001", json=update_data)
    assert resp.status_code == 200
    result = resp.json()
    assert result["name"] == "Updated Name"


@pytest.mark.asyncio
async def test_update_entity_not_found(client: AsyncClient, session: AsyncSession):
    update_data = {"name": "Updated Name"}
    resp = await client.put("/test/notfound", json=update_data)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_entity(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data()
    resp = await client.post("/entities", json=data)
    assert resp.status_code == 201

    resp = await client.delete("/test/test-001")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


@pytest.mark.asyncio
async def test_delete_entity_not_found(client: AsyncClient, session: AsyncSession):
    resp = await client.delete("/test/notfound")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_bulk_upsert(client: AsyncClient, session: AsyncSession):
    entities = [
        _make_entity_data(source_id="bulk-001", name="Bulk 1"),
        _make_entity_data(source_id="bulk-002", name="Bulk 2"),
    ]
    resp = await client.post("/entities/bulk", json=entities)
    assert resp.status_code == 201
    results = resp.json()
    assert len(results) == 2
    names = {r["name"] for r in results}
    assert "Bulk 1" in names
    assert "Bulk 2" in names


@pytest.mark.asyncio
async def test_bulk_upsert_update_existing(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data(source_id="upsert-001", name="Original")
    resp = await client.post("/entities", json=data)
    assert resp.status_code == 201

    entities = [_make_entity_data(source_id="upsert-001", name="Updated")]
    resp = await client.post("/entities/bulk", json=entities)
    assert resp.status_code == 201
    results = resp.json()
    assert len(results) == 1
    assert results[0]["name"] == "Updated"


@pytest.mark.asyncio
async def test_create_media(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data()
    resp = await client.post("/entities", json=data)
    assert resp.status_code == 201
    entity_id = resp.json()["id"]

    media_data = {
        "entity_id": entity_id,
        "media_type": "image",
        "url": "https://example.com/photo.jpg",
        "name": "Test Photo",
    }
    resp = await client.post("/media", json=media_data)
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
    resp = await client.post("/media", json=media_data)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_media(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data()
    resp = await client.post("/entities", json=data)
    assert resp.status_code == 201
    entity_id = resp.json()["id"]

    media_data = {
        "entity_id": entity_id,
        "media_type": "image",
        "url": "https://example.com/photo.jpg",
    }
    resp = await client.post("/media", json=media_data)
    assert resp.status_code == 201
    media_id = resp.json()["id"]

    resp = await client.delete(f"/media/{media_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


@pytest.mark.asyncio
async def test_delete_media_not_found(client: AsyncClient, session: AsyncSession):
    resp = await client.delete("/media/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_classification(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data()
    resp = await client.post("/entities", json=data)
    assert resp.status_code == 201
    entity_id = resp.json()["id"]

    classif_data = {
        "entity_id": entity_id,
        "category": "tourism",
        "value_code": "museum",
        "value_title": "Museum",
    }
    resp = await client.post("/classifications", json=classif_data)
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
    resp = await client.post("/classifications", json=classif_data)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_write_invalidates_cache(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data(source_id="cache-test", name="Cache Test")
    resp = await client.post("/entities", json=data)
    assert resp.status_code == 201

    resp = await client.get("/search?q=Cache+Test")
    assert resp.status_code == 200
    resp.json()

    update_data = {"name": "Cache Test Updated"}
    resp = await client.put("/test/cache-test", json=update_data)
    assert resp.status_code == 200

    resp = await client.get("/search?q=Cache+Test+Updated")
    assert resp.status_code == 200
    new_results = resp.json()
    assert any("Cache Test Updated" in r.get("name", "") for r in new_results.get("results", []))
