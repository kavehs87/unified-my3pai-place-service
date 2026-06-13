from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.config import settings
from dmo.models.database import Entity

WRITE_HEADERS = {"X-API-Key": settings.api_key}


@pytest.mark.asyncio
async def test_404_detail_error_format(client: AsyncClient):
    resp = await client.get("/unknown/source-123")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "NotFound"
    assert data["code"] == 404
    assert "message" in data
    assert "request_id" in data
    assert len(data["request_id"]) > 0


@pytest.mark.asyncio
async def test_404_delete_entity_error_format(client: AsyncClient):
    resp = await client.delete("/unknown/source-123", headers=WRITE_HEADERS)
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "NotFound"
    assert data["code"] == 404


@pytest.mark.asyncio
async def test_404_delete_media_error_format(client: AsyncClient):
    resp = await client.delete("/media/99999", headers=WRITE_HEADERS)
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "NotFound"
    assert data["code"] == 404


@pytest.mark.asyncio
async def test_409_create_entity_conflict(client: AsyncClient, session: AsyncSession):
    entity = Entity(
        id=uuid4(),
        source="test",
        source_id="conflict-test",
        name="Conflict Test",
        place_type="poi",
    )
    session.add(entity)
    await session.commit()

    resp = await client.post("/entities", json={
        "source": "test",
        "source_id": "conflict-test",
        "name": "Duplicate",
        "place_type": "poi",
    }, headers=WRITE_HEADERS)
    assert resp.status_code == 409
    data = resp.json()
    assert data["error"] == "Conflict"
    assert data["code"] == 409


@pytest.mark.asyncio
async def test_409_update_entity_not_found(client: AsyncClient):
    resp = await client.put("/nonexistent/source-999", json={
        "name": "Updated Name"
    }, headers=WRITE_HEADERS)
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "NotFound"


@pytest.mark.asyncio
async def test_422_validation_error_format(client: AsyncClient):
    resp = await client.get("/search?q=" + "x" * 501)
    assert resp.status_code == 422
    data = resp.json()
    assert "detail" in data or "message" in data


@pytest.mark.asyncio
async def test_422_invalid_lat(client: AsyncClient):
    resp = await client.get("/nearby?lat=999&lon=7.45&radius_km=10")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_422_invalid_lon(client: AsyncClient):
    resp = await client.get("/nearby?lat=46.95&lon=999&radius_km=10")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_422_invalid_radius(client: AsyncClient):
    resp = await client.get("/nearby?lat=46.95&lon=7.45&radius_km=-5")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_422_invalid_bbox_format(client: AsyncClient):
    resp = await client.get("/map?bbox=7.4,46.9")
    assert resp.status_code == 422
    data = resp.json()
    assert "detail" in data or "message" in data


@pytest.mark.asyncio
async def test_422_invalid_bbox_values(client: AsyncClient):
    resp = await client.get("/map?bbox=abc,def,ghi,jkl")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_422_bbox_out_of_range(client: AsyncClient):
    resp = await client.get("/map?bbox=200,100,300,200")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_422_bbox_min_greater_than_max(client: AsyncClient):
    resp = await client.get("/map?bbox=7.5,47.0,7.4,46.9")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_request_id_in_error_response(client: AsyncClient):
    resp = await client.get("/unknown/source-123")
    assert resp.status_code == 404
    data = resp.json()
    assert "request_id" in data
    assert len(data["request_id"]) > 0
    assert resp.headers.get("X-Request-ID") == data["request_id"]


@pytest.mark.asyncio
async def test_request_id_header_propagated(client: AsyncClient):
    custom_id = "my-custom-request-id-123"
    resp = await client.get(
        "/unknown/source-123",
        headers={"X-Request-ID": custom_id}
    )
    assert resp.status_code == 404
    data = resp.json()
    assert data["request_id"] == custom_id


@pytest.mark.asyncio
async def test_404_create_media_entity_not_found(client: AsyncClient):
    resp = await client.post("/media", json={
        "entity_id": "00000000-0000-0000-0000-000000000000",
        "url": "https://example.com/image.jpg",
        "media_type": "image",
    }, headers=WRITE_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_404_create_classification_entity_not_found(client: AsyncClient):
    resp = await client.post("/classifications", json={
        "entity_id": "00000000-0000-0000-0000-000000000000",
        "category": "accessibility",
        "value_code": "wheelchair",
        "value_title": "Wheelchair accessible",
    }, headers=WRITE_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_422_page_size_too_large(client: AsyncClient):
    resp = await client.get("/search?page_size=101")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_422_page_size_zero(client: AsyncClient):
    resp = await client.get("/search?page_size=0")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_422_cursor_max_length(client: AsyncClient):
    long_cursor = "a" * 501
    resp = await client.get(f"/search?cursor={long_cursor}")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_405_method_not_allowed(client: AsyncClient):
    resp = await client.post("/search")
    assert resp.status_code == 405


@pytest.mark.asyncio
async def test_404_unknown_route(client: AsyncClient):
    resp = await client.get("/nonexistent-route")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_error_response_has_all_required_fields(client: AsyncClient):
    resp = await client.get("/unknown/source-123")
    assert resp.status_code == 404
    data = resp.json()
    assert "error" in data
    assert "message" in data
    assert "code" in data
    assert "request_id" in data
    assert isinstance(data["error"], str)
    assert isinstance(data["message"], str)
    assert isinstance(data["code"], int)
    assert isinstance(data["request_id"], str)
