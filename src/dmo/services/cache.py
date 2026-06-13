import asyncio
import hashlib
import json

import redis.asyncio as redis
import structlog

from dmo.config import settings
from dmo.metrics import CACHE_HITS, CACHE_MISSES

_cache: redis.Redis | None = None
_cache_lock = asyncio.Lock()
logger = structlog.get_logger()


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
    try:
        client = await get_cache()
        key = _make_key(endpoint, params)
        value = await client.get(key)
        if value is not None:
            CACHE_HITS.inc()
        else:
            CACHE_MISSES.inc()
        return value
    except (redis.ConnectionError, redis.RedisError):
        return None


async def cache_set(
    endpoint: str,
    params: dict[str, str | int | float | None],
    value: str,
    ttl: int | None = None,
) -> None:
    try:
        client = await get_cache()
        key = _make_key(endpoint, params)
        await client.set(key, value, ex=ttl or settings.cache_ttl)
    except (redis.ConnectionError, redis.RedisError) as e:
        logger.error("cache_set_failed", endpoint=endpoint, error=str(e))


async def cache_delete_pattern(pattern: str) -> None:
    try:
        client = await get_cache()
        async for key in client.scan_iter(match=pattern):
            await client.delete(key)
    except (redis.ConnectionError, redis.RedisError) as e:
        logger.error("cache_delete_pattern_failed", pattern=pattern, error=str(e))


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
