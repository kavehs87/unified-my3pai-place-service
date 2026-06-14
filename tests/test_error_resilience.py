"""Error resilience tests: Redis failures, database errors, edge cases."""
from unittest.mock import patch

import pytest
import redis.asyncio as redis
from httpx import AsyncClient


class TestRedisFailure:
    """Test that the service handles Redis failures gracefully."""

    @pytest.mark.asyncio
    async def test_search_works_when_redis_down(self, client: AsyncClient):
        """Search returns results even when Redis is unavailable."""
        with patch("dmo.services.cache.get_cache") as mock_cache:
            mock_cache.side_effect = redis.ConnectionError("Redis is down")
            resp = await client.get("/search?q=test")
            # Should still return 200 with results (cache miss fallback)
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_nearby_works_when_redis_down(self, client: AsyncClient):
        """Nearby returns results even when Redis is unavailable."""
        with patch("dmo.services.cache.get_cache") as mock_cache:
            mock_cache.side_effect = redis.ConnectionError("Redis is down")
            resp = await client.get("/nearby?lat=46.95&lon=7.45&radius_km=10")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_map_works_when_redis_down(self, client: AsyncClient):
        """Map returns results even when Redis is unavailable."""
        with patch("dmo.services.cache.get_cache") as mock_cache:
            mock_cache.side_effect = redis.ConnectionError("Redis is down")
            resp = await client.get("/map?bbox=7.4,46.9,7.5,47.0")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_detail_works_when_redis_down(self, client: AsyncClient):
        """Detail returns results even when Redis is unavailable."""
        with patch("dmo.services.cache.get_cache") as mock_cache:
            mock_cache.side_effect = redis.ConnectionError("Redis is down")
            resp = await client.get("/test/notfound")
            # Should return 404, not 500
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_health_detects_redis_down(self, client: AsyncClient):
        """Health check reports Redis as down when unavailable."""
        with patch("dmo.api.health.get_cache") as mock_cache:
            mock_cache.side_effect = redis.ConnectionError("Redis is down")
            resp = await client.get("/health")
            data = resp.json()
            # Health returns degraded with redis: down component
            assert data["status"] == "degraded"
            assert data["components"]["redis"] == "down"


class TestPaginationEdgeCases:
    """Test pagination edge cases."""

    @pytest.mark.asyncio
    async def test_search_empty_results(self, client: AsyncClient):
        """Search with no matches returns empty results."""
        resp = await client.get("/search?q=thisdoesnotexistxyz123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["total"] == 0
        assert data["has_more"] is False
        assert data["next_cursor"] is None

    @pytest.mark.asyncio
    async def test_nearby_empty_results(self, client: AsyncClient):
        """Nearby with no entities in radius returns empty results."""
        resp = await client.get("/nearby?lat=0&lon=0&radius_km=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["total"] == 0
        assert data["has_more"] is False

    @pytest.mark.asyncio
    async def test_map_empty_results(self, client: AsyncClient):
        """Map with no entities in bbox returns empty results."""
        resp = await client.get("/map?bbox=-180,-90,-179,-89")
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_search_page_size_one(self, client: AsyncClient):
        """Search with page_size=1 returns single item."""
        resp = await client.get("/search?page_size=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) <= 1

    @pytest.mark.asyncio
    async def test_cursor_pagination_single_item(self, client: AsyncClient):
        """Cursor pagination with single item has no next_cursor."""
        resp = await client.get("/search?page_size=1")
        assert resp.status_code == 200
        data = resp.json()
        if not data["results"]:
            assert data["next_cursor"] is None
        elif data["has_more"]:
            assert data["next_cursor"] is not None


class TestValidationErrorResilience:
    """Test that validation errors don't crash the service."""

    @pytest.mark.asyncio
    async def test_invalid_lat_returns_422(self, client: AsyncClient):
        """Invalid latitude returns 422."""
        resp = await client.get("/nearby?lat=999&lon=7.45&radius_km=10")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_lon_returns_422(self, client: AsyncClient):
        """Invalid longitude returns 422."""
        resp = await client.get("/nearby?lat=46.95&lon=999&radius_km=10")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_negative_radius_returns_422(self, client: AsyncClient):
        """Negative radius returns 422."""
        resp = await client.get("/nearby?lat=46.95&lon=7.45&radius_km=-1")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_bbox_returns_422(self, client: AsyncClient):
        """Invalid bbox format returns 422."""
        resp = await client.get("/map?bbox=invalid")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_page_size_zero_returns_422(self, client: AsyncClient):
        """Page size of 0 returns 422."""
        resp = await client.get("/search?page_size=0")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_page_size_negative_returns_422(self, client: AsyncClient):
        """Negative page size returns 422."""
        resp = await client.get("/search?page_size=-1")
        assert resp.status_code == 422
