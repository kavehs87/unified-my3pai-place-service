"""Tests for unified categories endpoint and unified_category filter."""

import pytest
from httpx import ASGITransport, AsyncClient

from dmo.main import app


@pytest.fixture(autouse=True)
def disable_cache(monkeypatch):
    import dmo.services.cache as cache_module

    async def fake_get_or_set(endpoint, params, fetch_fn, ttl=None):
        result = await fetch_fn()
        return result, "MISS"

    monkeypatch.setattr(cache_module, "cache_get_or_set", fake_get_or_set)


@pytest.mark.asyncio
async def test_unified_categories_returns_tree():
    """Test that /unified-categories returns hierarchical tree."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/unified-categories")
        assert resp.status_code == 200
        data = resp.json()
        assert "categories" in data
        assert isinstance(data["categories"], list)
        assert len(data["categories"]) > 0

        top = data["categories"][0]
        assert "id" in top
        assert "slug" in top
        assert "name" in top
        assert "children" in top
        assert "count" in top
        assert top["parent_id"] is None


@pytest.mark.asyncio
async def test_unified_categories_has_children():
    """Test that top-level categories have nested children."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/unified-categories")
        data = resp.json()

        has_children = False
        for cat in data["categories"]:
            if cat["children"]:
                has_children = True
                child = cat["children"][0]
                assert "id" in child
                assert "slug" in child
                assert "parent_id" in child
                assert child["parent_id"] == cat["id"]
                break
        assert has_children, "Expected at least one category with children"


@pytest.mark.asyncio
async def test_unified_categories_includes_counts():
    """Test that categories include entity counts."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/unified-categories")
        data = resp.json()

        for cat in data["categories"]:
            assert isinstance(cat["count"], int)
            assert cat["count"] >= 0
            for child in cat["children"]:
                assert isinstance(child["count"], int)
                assert child["count"] >= 0


@pytest.mark.asyncio
async def test_search_filter_unified_category_top_level():
    """Test search with unified_category=top-level slug."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/search", params={"unified_category": "food_drink", "page_size": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        for item in data["results"]:
            assert item.get("unified_category") == "food_drink"


@pytest.mark.asyncio
async def test_search_filter_unified_category_leaf():
    """Test search with unified_category=leaf slug."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/search", params={"unified_category": "restaurant", "page_size": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        for item in data["results"]:
            assert item.get("unified_subcategory") == "restaurant"


@pytest.mark.asyncio
async def test_search_filter_unified_category_nonexistent():
    """Test search with nonexistent unified_category returns empty."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/search", params={"unified_category": "nonexistent_category_xyz"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["total"] == 0


@pytest.mark.asyncio
async def test_nearby_filter_unified_category():
    """Test nearby with unified_category filter."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/nearby",
            params={
                "lat": 47.0,
                "lon": 8.0,
                "radius_km": 100,
                "unified_category": "accommodation",
                "page_size": 10,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        for item in data["results"]:
            assert item.get("unified_category") == "accommodation"


@pytest.mark.asyncio
async def test_map_filter_unified_category():
    """Test map with unified_category filter."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/map",
            params={
                "bbox": "7.0,46.0,9.0,48.0",
                "unified_category": "attraction",
                "page_size": 10,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        for item in data["results"]:
            assert item.get("unified_category") == "attraction"


@pytest.mark.asyncio
async def test_entity_list_item_includes_unified_fields():
    """Test that EntityListItem includes unified_category and unified_subcategory."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/search", params={"page_size": 1})
        assert resp.status_code == 200
        data = resp.json()
        if data["results"]:
            item = data["results"][0]
            assert "unified_category" in item
            assert "unified_subcategory" in item


@pytest.mark.asyncio
async def test_unified_categories_cache_header():
    """Test that /unified-categories returns cache status header."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/unified-categories")
        assert resp.status_code == 200
        assert "X-Cache-Status" in resp.headers


@pytest.mark.asyncio
async def test_search_combined_filters():
    """Test search with unified_category combined with other filters."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/search",
            params={
                "unified_category": "food_drink",
                "country": "CH",
                "page_size": 10,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["results"]:
            assert item.get("unified_category") == "food_drink"
            assert item.get("country") == "CH"
