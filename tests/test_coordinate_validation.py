import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.config import settings
from dmo.models.schemas import EntityCreate, EntityUpdate

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


class TestEntityCreateCoordinateValidation:
    def test_create_with_only_latitude_fails(self):
        with pytest.raises(ValueError, match="both latitude and longitude"):
            EntityCreate(
                source="test",
                source_id="test-001",
                name="Test",
                place_type="poi",
                latitude=46.95,
            )

    def test_create_with_only_longitude_fails(self):
        with pytest.raises(ValueError, match="both latitude and longitude"):
            EntityCreate(
                source="test",
                source_id="test-001",
                name="Test",
                place_type="poi",
                longitude=7.45,
            )

    def test_create_with_both_coordinates_succeeds(self):
        entity = EntityCreate(
            source="test",
            source_id="test-001",
            name="Test",
            place_type="poi",
            latitude=46.95,
            longitude=7.45,
        )
        assert entity.latitude == 46.95
        assert entity.longitude == 7.45

    def test_create_with_neither_coordinate_succeeds(self):
        entity = EntityCreate(
            source="test",
            source_id="test-001",
            name="Test",
            place_type="poi",
        )
        assert entity.latitude is None
        assert entity.longitude is None


class TestEntityUpdateCoordinateValidation:
    def test_update_with_only_latitude_fails(self):
        with pytest.raises(ValueError, match="both latitude and longitude"):
            EntityUpdate(latitude=46.95)

    def test_update_with_only_longitude_fails(self):
        with pytest.raises(ValueError, match="both latitude and longitude"):
            EntityUpdate(longitude=7.45)

    def test_update_with_both_coordinates_succeeds(self):
        update = EntityUpdate(latitude=46.95, longitude=7.45)
        assert update.latitude == 46.95
        assert update.longitude == 7.45

    def test_update_with_neither_coordinate_succeeds(self):
        update = EntityUpdate(name="Updated Name")
        assert update.latitude is None
        assert update.longitude is None


@pytest.mark.asyncio
async def test_create_entity_only_latitude_returns_422(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data(latitude=46.95, longitude=None)
    data.pop("longitude", None)
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_entity_only_longitude_returns_422(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data(latitude=None, longitude=7.45)
    data.pop("latitude", None)
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_entity_no_coordinates_returns_201(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data(source_id="no-coords", latitude=None, longitude=None)
    data.pop("latitude", None)
    data.pop("longitude", None)
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_update_entity_only_latitude_returns_422(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data(source_id="upd-test")
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 201

    update_data = {"latitude": 47.0}
    resp = await client.put("/test/upd-test", json=update_data, headers=WRITE_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_entity_only_longitude_returns_422(client: AsyncClient, session: AsyncSession):
    data = _make_entity_data(source_id="upd-test-2")
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 201

    update_data = {"longitude": 8.0}
    resp = await client.put("/test/upd-test-2", json=update_data, headers=WRITE_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_entity_both_coordinates_returns_200(
    client: AsyncClient, session: AsyncSession
):
    data = _make_entity_data(source_id="upd-test-3")
    resp = await client.post("/entities", json=data, headers=WRITE_HEADERS)
    assert resp.status_code == 201

    update_data = {"latitude": 47.0, "longitude": 8.0}
    resp = await client.put("/test/upd-test-3", json=update_data, headers=WRITE_HEADERS)
    assert resp.status_code == 200
