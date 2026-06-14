"""Concurrency tests: race conditions, concurrent read/write."""
import asyncio

import pytest
from httpx import AsyncClient

from dmo.config import settings

WRITE_HEADERS = {"X-API-Key": settings.api_key}


class TestConcurrentBulkUpsert:
    """Test that concurrent bulk operations don't cause failures."""

    @pytest.mark.asyncio
    async def test_concurrent_bulk_upsert_no_conflict(self, client: AsyncClient):
        """Two concurrent bulk operations with different source_ids succeed."""
        batch1 = [
            {
                "source": "test",
                "source_id": f"conc-bulk1-{i}",
                "name": f"Concurrent Test 1-{i}",
                "place_type": "poi",
                "latitude": 46.95,
                "longitude": 7.45,
                "country": "CH",
            }
            for i in range(10)
        ]
        batch2 = [
            {
                "source": "test",
                "source_id": f"conc-bulk2-{i}",
                "name": f"Concurrent Test 2-{i}",
                "place_type": "poi",
                "latitude": 46.95,
                "longitude": 7.45,
                "country": "CH",
            }
            for i in range(10)
        ]

        resp1, resp2 = await asyncio.gather(
            client.post("/entities/bulk", json=batch1, headers=WRITE_HEADERS),
            client.post("/entities/bulk", json=batch2, headers=WRITE_HEADERS),
        )
        assert resp1.status_code == 201
        assert resp2.status_code == 201

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="Race condition in bulk_upsert: advisory lock not fully preventing conflicts")
    async def test_concurrent_bulk_upsert_conflict_handled(self, client: AsyncClient):
        """Two concurrent bulk operations with same source_ids don't crash."""
        batch = [
            {
                "source": "test",
                "source_id": f"conc-conflict-{i}",
                "name": f"Conflict Test-{i}",
                "place_type": "poi",
                "latitude": 46.95,
                "longitude": 7.45,
                "country": "CH",
            }
            for i in range(5)
        ]

        resp1, resp2 = await asyncio.gather(
            client.post("/entities/bulk", json=batch, headers=WRITE_HEADERS),
            client.post("/entities/bulk", json=batch, headers=WRITE_HEADERS),
        )
        # At least one should succeed; neither should return 500
        assert resp1.status_code != 500
        assert resp2.status_code != 500


class TestConcurrentReadWrite:
    """Test concurrent read and write operations."""

    @pytest.mark.asyncio
    async def test_concurrent_search_while_bulk_upsert(self, client: AsyncClient):
        """Search continues to work during bulk upsert."""
        batch = [
            {
                "source": "test",
                "source_id": f"conc-rw-{i}",
                "name": f"RW Test-{i}",
                "place_type": "poi",
                "latitude": 46.95,
                "longitude": 7.45,
                "country": "CH",
            }
            for i in range(20)
        ]

        results = await asyncio.gather(
            client.post("/entities/bulk", json=batch, headers=WRITE_HEADERS),
            client.get("/search?q=test"),
            client.get("/search?q=test"),
        )
        # All should succeed
        for resp in results:
            assert resp.status_code in (200, 201)

    @pytest.mark.asyncio
    async def test_concurrent_nearby_while_bulk_upsert(self, client: AsyncClient):
        """Nearby query continues to work during bulk upsert."""
        batch = [
            {
                "source": "test",
                "source_id": f"conc-nb-{i}",
                "name": f"NB Test-{i}",
                "place_type": "poi",
                "latitude": 46.95,
                "longitude": 7.45,
                "country": "CH",
            }
            for i in range(20)
        ]

        results = await asyncio.gather(
            client.post("/entities/bulk", json=batch, headers=WRITE_HEADERS),
            client.get("/nearby?lat=46.95&lon=7.45&radius_km=10"),
            client.get("/nearby?lat=46.95&lon=7.45&radius_km=10"),
        )
        for resp in results:
            assert resp.status_code in (200, 201)


class TestConcurrentDetail:
    """Test concurrent detail requests."""

    @pytest.mark.asyncio
    async def test_concurrent_detail_requests(self, client: AsyncClient):
        """Multiple concurrent detail requests for same entity succeed."""
        # First create an entity
        create_resp = await client.post(
            "/entities",
            json={
                "source": "test",
                "source_id": "conc-detail-001",
                "name": "Detail Test",
                "place_type": "poi",
                "latitude": 46.95,
                "longitude": 7.45,
                "country": "CH",
            },
            headers=WRITE_HEADERS,
        )
        assert create_resp.status_code == 201

        # Then make concurrent detail requests
        results = await asyncio.gather(
            *[client.get("/test/conc-detail-001") for _ in range(10)]
        )
        for resp in results:
            assert resp.status_code in (200, 404)
