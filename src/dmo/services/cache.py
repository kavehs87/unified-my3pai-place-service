import asyncio
import hashlib
import json
from collections.abc import Callable, Coroutine

import redis.asyncio as redis
import structlog

from dmo.config import settings
from dmo.metrics import CACHE_HITS, CACHE_MISSES

_cache: redis.Redis | None = None
_cache_lock = asyncio.Lock()
logger = structlog.get_logger()

_LOCK_TIMEOUT = 5
_STAMPEDE_RETRY_DELAY = 0.05


async def get_cache() -> redis.Redis:
    global _cache
    if _cache is None:
        async with _cache_lock:
            if _cache is None:
                _cache = redis.from_url(settings.redis_url, decode_responses=True)
    return _cache


def _make_key(endpoint: str, params: dict[str, str | int | float | None]) -> str:
    sorted_params = json.dumps(params, sort_keys=True)
    param_hash = hashlib.sha256(sorted_params.encode()).hexdigest()
    return f"dmo:{endpoint}:{param_hash}"


async def cache_get(endpoint: str, params: dict[str, str | int | float | None]) -> str | None:
    client = await get_cache()
    key = _make_key(endpoint, params)
    value = await client.get(key)
    if value is not None:
        CACHE_HITS.inc()
    else:
        CACHE_MISSES.inc()
    return value


async def cache_set(
    endpoint: str,
    params: dict[str, str | int | float | None],
    value: str,
    ttl: int | None = None,
) -> None:
    client = await get_cache()
    key = _make_key(endpoint, params)
    await client.set(key, value, ex=ttl or settings.cache_ttl)


async def cache_delete_pattern(pattern: str) -> None:
    client = await get_cache()
    async for key in client.scan_iter(match=pattern):
        await client.delete(key)


async def cache_get_or_set(
    endpoint: str,
    params: dict[str, str | int | float | None],
    fetch_fn: Callable[[], Coroutine[None, None, str]],
    ttl: int | None = None,
) -> str | None:
    """Get from cache, or acquire lock and fetch. Prevents stampede on cache miss."""
    client = await get_cache()
    key = _make_key(endpoint, params)
    lock_key = f"{key}:lock"

    # Try cache
    value = await client.get(key)
    if value is not None:
        CACHE_HITS.inc()
        return value

    CACHE_MISSES.inc()

    # Try to acquire lock (SET NX)
    acquired = await client.set(lock_key, "1", nx=True, ex=_LOCK_TIMEOUT)
    if not acquired:
        # Another request is fetching — wait and retry cache
        await asyncio.sleep(_STAMPEDE_RETRY_DELAY)
        value = await client.get(key)
        if value is not None:
            return value
        # Lock may be stale — fall through to fetch without lock
        return None

    try:
        # Double-check cache after acquiring lock
        value = await client.get(key)
        if value is not None:
            return value

        # Fetch, cache, return
        result = await fetch_fn()
        await client.set(key, result, ex=ttl or settings.cache_ttl)
        return result
    finally:
        await client.delete(lock_key)


def _cache_task_done(task: asyncio.Task) -> None:
    exc = task.exception()
    if exc:
        logger.error("cache_task_failed", error=str(exc))


async def cache_set_async(
    endpoint: str,
    params: dict[str, str | int | float | None],
    value: str,
    ttl: int | None = None,
) -> None:
    task = asyncio.create_task(cache_set(endpoint, params, value, ttl=ttl))
    task.add_done_callback(_cache_task_done)
