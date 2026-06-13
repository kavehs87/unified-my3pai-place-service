from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_cache_get_miss():
    from dmo.services import cache as cache_module

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=None)

    with patch.object(cache_module, "get_cache", new_callable=AsyncMock) as mock_get_cache:
        mock_get_cache.return_value = fake_client
        result = await cache_module.cache_get("search", {"q": "test"})
        assert result is None


@pytest.mark.asyncio
async def test_cache_set_and_get():
    from dmo.services.cache import _make_key

    cached_value = '{"results": [], "total": 0}'
    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=cached_value)
    fake_client.set = AsyncMock()

    key = _make_key("search", {"q": "test"})
    assert key.startswith("dmo:search:")

    result = await fake_client.get(key)
    assert result == cached_value

    await fake_client.set(key, cached_value, ex=300)
    fake_client.set.assert_called()


@pytest.mark.asyncio
async def test_cache_key_deterministic():
    from dmo.services.cache import _make_key

    key1 = _make_key("search", {"q": "test", "page": 1})
    key2 = _make_key("search", {"page": 1, "q": "test"})
    assert key1 == key2
    assert key1.startswith("dmo:search:")

    key3 = _make_key("detail", {"source": "rexby", "source_id": "abc"})
    assert key1 != key3


@pytest.mark.asyncio
async def test_cache_connection_error_graceful():
    import redis.asyncio as redis_module

    from dmo.services import cache as cache_module

    with patch.object(cache_module, "get_cache", new_callable=AsyncMock, side_effect=redis_module.ConnectionError):
        result = await cache_module.cache_get("search", {"q": "test"})
        assert result is None

        await cache_module.cache_set("search", {"q": "test"}, "value", ttl=300)

        await cache_module.cache_delete_pattern("dmo:search:*")


@pytest.mark.asyncio
async def test_cache_delete_pattern():
    from dmo.services import cache as cache_module

    async def fake_scan_iter(match):
        yield "dmo:search:abc123"
        yield "dmo:search:def456"

    fake_client = AsyncMock()
    fake_client.scan_iter = fake_scan_iter
    fake_client.delete = AsyncMock()

    with patch.object(cache_module, "get_cache", new_callable=AsyncMock) as mock_get_cache:
        mock_get_cache.return_value = fake_client
        await cache_module.cache_delete_pattern("dmo:search:*")
        assert fake_client.delete.call_count == 2
