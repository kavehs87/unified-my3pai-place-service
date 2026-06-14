import pytest
from httpx import AsyncClient

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


class TestArrayFieldValidation:
    """Test max_length validation on secondary_types and region_names."""

    def test_secondary_types_max_length_exceeded(self):
        with pytest.raises(ValueError):
            EntityCreate(
                **_make_entity_data(
                    secondary_types=["type"] * 101
                )
            )

    def test_region_names_max_length_exceeded(self):
        with pytest.raises(ValueError):
            EntityCreate(
                **_make_entity_data(
                    region_names=["region"] * 101
                )
            )

    def test_secondary_types_valid(self):
        entity = EntityCreate(
            **_make_entity_data(
                secondary_types=["museum", "gallery"]
            )
        )
        assert entity.secondary_types == ["museum", "gallery"]

    def test_region_names_valid(self):
        entity = EntityCreate(
            **_make_entity_data(
                region_names=["Zurich", "Switzerland"]
            )
        )
        assert entity.region_names == ["Zurich", "Switzerland"]

    def test_update_secondary_types_max_length_exceeded(self):
        with pytest.raises(ValueError):
            EntityUpdate(
                secondary_types=["type"] * 101
            )

    def test_update_region_names_max_length_exceeded(self):
        with pytest.raises(ValueError):
            EntityUpdate(
                region_names=["region"] * 101
            )

    @pytest.mark.asyncio
    async def test_create_rejects_long_secondary_types(self, client: AsyncClient, session):
        resp = await client.post(
            "/entities",
            json=_make_entity_data(
                secondary_types=["type"] * 101
            ),
            headers=WRITE_HEADERS,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_rejects_long_region_names(self, client: AsyncClient, session):
        resp = await client.post(
            "/entities",
            json=_make_entity_data(
                region_names=["region"] * 101
            ),
            headers=WRITE_HEADERS,
        )
        assert resp.status_code == 422


class TestBulkSizeLimit:
    """Test max_length validation on bulk upsert."""

    @pytest.mark.asyncio
    async def test_bulk_rejects_over_1000_items(self, client: AsyncClient, session):
        entities = [_make_entity_data(source_id=f"bulk-{i}") for i in range(1001)]
        resp = await client.post(
            "/entities/bulk",
            json=entities,
            headers=WRITE_HEADERS,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_bulk_accepts_exactly_1000_items(self, client: AsyncClient, session):
        entities = [_make_entity_data(source_id=f"bulk-{i}") for i in range(1000)]
        resp = await client.post(
            "/entities/bulk",
            json=entities,
            headers=WRITE_HEADERS,
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_bulk_accepts_fewer_than_1000_items(self, client: AsyncClient, session):
        entities = [_make_entity_data(source_id=f"bulk-{i}") for i in range(50)]
        resp = await client.post(
            "/entities/bulk",
            json=entities,
            headers=WRITE_HEADERS,
        )
        assert resp.status_code == 201
        assert len(resp.json()) == 50
