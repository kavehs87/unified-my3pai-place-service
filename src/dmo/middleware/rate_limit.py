import time
import uuid

import redis.asyncio as redis
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from dmo.config import settings
from dmo.services.cache import get_cache


def _rate_limit_response(request: Request) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "")
    return JSONResponse(
        status_code=429,
        content={
            "error": "TooManyRequests",
            "message": "Rate limit exceeded. Try again later.",
            "code": 429,
            "request_id": request_id,
        },
        headers={"Retry-After": str(settings.rate_limit_window_seconds)},
    )


class RateLimiterMiddleware(BaseHTTPMiddleware):
    def _get_client_ip(self, request: Request) -> str:
        if settings.trust_proxy_headers:
            forwarded_for = request.headers.get("X-Forwarded-For", "")
            if forwarded_for:
                return forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not settings.rate_limit_enabled:
            return await call_next(request)

        if request.url.path in ("/health", "/metrics"):
            return await call_next(request)

        try:
            client: redis.Redis = await get_cache()
            now = time.time()
            window_start = now - settings.rate_limit_window_seconds
            client_ip = self._get_client_ip(request)
            key = f"ratelimit:{client_ip}"

            pipeline = client.pipeline()
            pipeline.zremrangebyscore(key, 0, window_start)
            pipeline.zcard(key)
            pipeline.expire(key, settings.rate_limit_window_seconds + 1)
            results = await pipeline.execute()

            current_requests = results[1]

            if current_requests >= settings.rate_limit_max_requests:
                return _rate_limit_response(request)

            pipeline = client.pipeline()
            member = f"{now}:{uuid.uuid4()}"
            pipeline.zadd(key, {member: now})
            pipeline.expire(key, settings.rate_limit_window_seconds + 1)
            await pipeline.execute()

            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_max_requests)
            response.headers["X-RateLimit-Remaining"] = str(
                max(0, settings.rate_limit_max_requests - current_requests - 1)
            )
            return response

        except redis.RedisError:
            # Any Redis failure (connection, timeout, etc.) — fail open
            return await call_next(request)
