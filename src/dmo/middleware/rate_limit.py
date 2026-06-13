import time

import redis.asyncio as redis
from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from dmo.config import settings
from dmo.services.cache import get_cache


class RateLimiterMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not settings.rate_limit_enabled:
            return await call_next(request)

        if request.url.path in ("/health", "/metrics"):
            return await call_next(request)

        try:
            client: redis.Redis = await get_cache()
            now = time.time()
            window_start = now - settings.rate_limit_window_seconds
            key = "ratelimit:global"

            pipeline = client.pipeline()
            pipeline.zremrangebyscore(key, 0, window_start)
            pipeline.zadd(key, {str(now): str(now)})
            pipeline.zcard(key)
            pipeline.expire(key, settings.rate_limit_window_seconds + 1)
            results = await pipeline.execute()

            current_requests = results[2]

            if current_requests > settings.rate_limit_max_requests:
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded. Try again later.",
                    headers={"Retry-After": str(settings.rate_limit_window_seconds)},
                )

            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_max_requests)
            response.headers["X-RateLimit-Remaining"] = str(
                max(0, settings.rate_limit_max_requests - current_requests)
            )
            return response

        except redis.ConnectionError:
            return await call_next(request)
