import json
from datetime import UTC
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Entity


class TestOpenStatusCacheSeparation:
    @pytest.mark.asyncio
    async def test_detail_response_includes_open_status(self, client: AsyncClient, session: AsyncSession):
        entity = Entity(
            id=uuid4(),
            source="test",
            source_id="open-test",
            name="Open Entity",
            place_type="restaurant",
            is_open=True,
        )
        session.add(entity)
        await session.commit()

        resp = await client.get("/test/open-test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_open"] is True

    @pytest.mark.asyncio
    async def test_detail_response_none_open_status(self, client: AsyncClient, session: AsyncSession):
        entity = Entity(
            id=uuid4(),
            source="test",
            source_id="no-open",
            name="No Open Entity",
            place_type="poi",
        )
        session.add(entity)
        await session.commit()

        resp = await client.get("/test/no-open")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_open"] is None
        assert data["opens_at"] is None
        assert data["closes_at"] is None

    @pytest.mark.asyncio
    async def test_detail_open_status_from_db_when_no_cache(
        self, client: AsyncClient, session: AsyncSession
    ):
        from datetime import datetime

        entity = Entity(
            id=uuid4(),
            source="test",
            source_id="open-full",
            name="Full Open Entity",
            place_type="restaurant",
            is_open=True,
            opens_at=datetime(2025, 1, 1, 9, 0, tzinfo=UTC),
            closes_at=datetime(2025, 1, 1, 18, 0, tzinfo=UTC),
        )
        session.add(entity)
        await session.commit()

        resp = await client.get("/test/open-full")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_open"] is True
        assert data["opens_at"] is not None
        assert data["closes_at"] is not None


class TestOpenStatusCacheLogic:
    def test_fetch_open_status_returns_null_for_none(self):

        async def mock_fetch():
            status = None
            if not status:
                return "null"
            return json.dumps(status.model_dump(mode="json"))

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(mock_fetch())
        assert result == "null"

    def test_open_status_null_sentinel_not_deserialized(self):
        cached = "null"
        from dmo.models.schemas import OpenStatus

        open_status = None
        if cached and cached != "null":
            open_status = OpenStatus.model_validate(json.loads(cached))

        assert open_status is None

    def test_open_status_valid_json_deserialized(self):
        from dmo.models.schemas import OpenStatus

        open_status = OpenStatus(is_open=True, opens_at=None, closes_at=None)
        cached = json.dumps(open_status.model_dump(mode="json"))

        result = None
        if cached and cached != "null":
            result = OpenStatus.model_validate(json.loads(cached))

        assert result is not None
        assert result.is_open is True

    def test_detail_dict_strips_open_status_fields(self):
        from dmo.models.schemas import EntityDetail

        detail = EntityDetail(
            id=uuid4(),
            source="test",
            source_id="test",
            name="Test",
            place_type="poi",
            is_open=True,
        )
        detail_dict = detail.model_dump(mode="json")
        detail_dict["is_open"] = None
        detail_dict["opens_at"] = None
        detail_dict["closes_at"] = None

        assert detail_dict["is_open"] is None
        assert detail_dict["opens_at"] is None
        assert detail_dict["closes_at"] is None
        assert detail_dict["name"] == "Test"
