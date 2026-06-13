import hashlib
import json

import redis.asyncio as redis

from dmo.config import settings
from dmo.metrics import CACHE_HITS, CACHE_MISSES

_cache: redis.Redis | None = None


async def get_cache() -> redis.Redis:
    global _cache
    if _cache is None:
        _cache = redis.from_url(settings.redis_url, decode_responses=True)
    return _cache


def _make_key(endpoint: str, params: dict[str, str | int | float | None]) -> str:
    sorted_params = json.dumps(params, sort_keys=True)
    param_hash = hashlib.md5(sorted_params.encode()).hexdigest()
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
    except (redis.ConnectionError, redis.RedisError):
        pass


async def cache_delete_pattern(pattern: str) -> None:
    try:
        client = await get_cache()
        async for key in client.scan_iter(match=pattern):
            await client.delete(key)
    except (redis.ConnectionError, redis.RedisError):
        pass
