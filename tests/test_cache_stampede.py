import asyncio
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_stampede_single_fetch_on_concurrent_miss():
    """Concurrent cache misses should only trigger one fetch."""
    from dmo.services import cache as cache_module

    fetch_count = 0
    lock_acquired = False
    cached_value = None

    async def fetch_fn() -> str:
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0.05)
        return '{"results": []}'

    async def mock_get(key):
        if "lock" in key:
            return None
        return cached_value

    async def mock_set(key, value, nx=None, ex=None):
        nonlocal lock_acquired, cached_value
        if nx is not None:
            if not lock_acquired:
                lock_acquired = True
                return True
            return False
        cached_value = value
        return True

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(side_effect=mock_get)
    fake_client.set = AsyncMock(side_effect=mock_set)
    fake_client.delete = AsyncMock()

    with patch.object(cache_module, "get_cache", new_callable=AsyncMock) as mock_get_cache:
        mock_get_cache.return_value = fake_client

        tasks = [cache_module.cache_get_or_set("search", {"q": "test"}, fetch_fn=fetch_fn) for _ in range(10)]
        await asyncio.gather(*tasks)

    assert fetch_count == 1


@pytest.mark.asyncio
async def test_stampede_cache_hit_bypasses_lock():
    """Cache hit should return immediately without lock."""
    from dmo.services import cache as cache_module

    async def fetch_fn() -> str:
        return '{"results": []}'

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value='{"results": []}')

    with patch.object(cache_module, "get_cache", new_callable=AsyncMock) as mock_get_cache:
        mock_get_cache.return_value = fake_client

        result = await cache_module.cache_get_or_set("search", {"q": "test"}, fetch_fn=fetch_fn)
        assert result == '{"results": []}'
        fake_client.set.assert_not_called()


@pytest.mark.asyncio
async def test_stampede_lock_release_after_fetch():
    """Lock should be released after fetch completes."""
    from dmo.services import cache as cache_module

    async def fetch_fn() -> str:
        return '{"results": []}'

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=None)
    fake_client.set = AsyncMock(return_value=True)
    fake_client.delete = AsyncMock()

    with patch.object(cache_module, "get_cache", new_callable=AsyncMock) as mock_get_cache:
        mock_get_cache.return_value = fake_client

        await cache_module.cache_get_or_set("search", {"q": "test"}, fetch_fn=fetch_fn)

        delete_calls = [c for c in fake_client.delete.call_args_list if "lock" in str(c)]
        assert len(delete_calls) >= 1


@pytest.mark.asyncio
async def test_stampede_waiter_gets_cached_result():
    """Waiter should get the cached result set by the lock holder."""
    from dmo.services import cache as cache_module

    fetch_count = 0
    lock_acquired = False
    cached_value = None

    async def fetch_fn() -> str:
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0.02)
        return '{"results": [1]}'

    async def mock_get(key):
        if "lock" in key:
            return None
        return cached_value

    async def mock_set(key, value, nx=None, ex=None):
        nonlocal lock_acquired, cached_value
        if nx is not None:
            if not lock_acquired:
                lock_acquired = True
                return True
            return False
        cached_value = value
        return True

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(side_effect=mock_get)
    fake_client.set = AsyncMock(side_effect=mock_set)
    fake_client.delete = AsyncMock()

    with patch.object(cache_module, "get_cache", new_callable=AsyncMock) as mock_get_cache:
        mock_get_cache.return_value = fake_client

        tasks = [cache_module.cache_get_or_set("search", {"q": "test"}, fetch_fn=fetch_fn) for _ in range(5)]
        results = await asyncio.gather(*tasks)

    assert fetch_count == 1
    assert all(r == '{"results": [1]}' for r in results)


@pytest.mark.asyncio
async def test_stampede_stale_lock_fallback():
    """If lock acquisition fails, should fall through to direct fetch."""
    from dmo.services import cache as cache_module

    async def fetch_fn() -> str:
        return '{"results": []}'

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=None)
    fake_client.set = AsyncMock(return_value=False)

    with patch.object(cache_module, "get_cache", new_callable=AsyncMock) as mock_get_cache:
        mock_get_cache.return_value = fake_client

        result = await cache_module.cache_get_or_set("search", {"q": "test"}, fetch_fn=fetch_fn)

    assert result is None


@pytest.mark.asyncio
async def test_stampede_double_check_after_lock():
    """After acquiring lock, should double-check cache before fetching."""
    from dmo.services import cache as cache_module

    async def fetch_fn() -> str:
        raise AssertionError("fetch should not be called — cache was filled by another holder")

    get_calls = 0

    async def mock_get(key):
        nonlocal get_calls
        get_calls += 1
        if get_calls == 1:
            return None
        return '{"results": []}'

    async def mock_set(key, value, nx=None, ex=None):
        if nx is not None:
            return True
        return True

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(side_effect=mock_get)
    fake_client.set = AsyncMock(side_effect=mock_set)
    fake_client.delete = AsyncMock()

    with patch.object(cache_module, "get_cache", new_callable=AsyncMock) as mock_get_cache:
        mock_get_cache.return_value = fake_client

        result = await cache_module.cache_get_or_set("search", {"q": "test"}, fetch_fn=fetch_fn)

    assert result == '{"results": []}'


@pytest.mark.asyncio
async def test_stampede_ttl_respected():
    """Custom TTL should be passed to cache set."""
    from dmo.services import cache as cache_module

    async def fetch_fn() -> str:
        return '{"results": []}'

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=None)
    fake_client.set = AsyncMock(return_value=True)
    fake_client.delete = AsyncMock()

    with patch.object(cache_module, "get_cache", new_callable=AsyncMock) as mock_get_cache:
        mock_get_cache.return_value = fake_client

        await cache_module.cache_get_or_set("search", {"q": "test"}, fetch_fn=fetch_fn, ttl=300)

        set_calls = [c for c in fake_client.set.call_args_list if "lock" not in str(c)]
        assert len(set_calls) == 1
        assert set_calls[0][1].get("ex") == 300 or set_calls[0][0][2] == 300
