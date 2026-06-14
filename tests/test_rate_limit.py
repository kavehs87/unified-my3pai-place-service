from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


class FakePipeline:
    """Fake Redis pipeline that returns controllable results."""

    def __init__(self, count: int = 1):
        self.count = count

    def zremrangebyscore(self, *args):
        return self

    def zadd(self, *args):
        return self

    def zcard(self, *args):
        return self

    def expire(self, *args):
        return self

    async def execute(self):
        return [0, True, self.count, True]


class FakeRedis:
    """Fake Redis client."""

    def __init__(self, count: int = 1):
        self.count = count

    def pipeline(self):
        return FakePipeline(self.count)


@pytest.mark.asyncio
async def test_rate_limit_disabled(client: AsyncClient, session):
    with patch("dmo.middleware.rate_limit.settings") as mock_settings:
        mock_settings.rate_limit_enabled = False
        mock_settings.redis_url = "redis://localhost:6379/0"

        resp = await client.get("/search")
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" not in resp.headers


@pytest.mark.asyncio
async def test_health_excluded_from_rate_limit(client: AsyncClient, session):
    with patch("dmo.middleware.rate_limit.settings") as mock_settings:
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_max_requests = 100
        mock_settings.rate_limit_window_seconds = 60
        mock_settings.redis_url = "redis://localhost:6379/0"

        resp = await client.get("/health")
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" not in resp.headers


@pytest.mark.asyncio
async def test_rate_limit_headers_present(client: AsyncClient, session):

    fake_redis = FakeRedis(count=1)
    fake_get_cache = AsyncMock(return_value=fake_redis)

    with patch("dmo.middleware.rate_limit.settings") as mock_settings:
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_max_requests = 100
        mock_settings.rate_limit_window_seconds = 60
        mock_settings.redis_url = "redis://localhost:6379/0"

        with patch("dmo.middleware.rate_limit.get_cache", fake_get_cache):
            resp = await client.get("/search")
            assert resp.status_code == 200
            assert resp.headers.get("X-RateLimit-Limit") == "100"
            assert resp.headers.get("X-RateLimit-Remaining") == "99"


@pytest.mark.asyncio
async def test_rate_limit_exceeded():
    from fastapi import Request
    from fastapi.exceptions import HTTPException

    from dmo.middleware.rate_limit import RateLimiterMiddleware

    fake_redis = FakeRedis(count=2)
    fake_get_cache = AsyncMock(return_value=fake_redis)

    with patch("dmo.middleware.rate_limit.settings") as mock_settings:
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_max_requests = 1
        mock_settings.rate_limit_window_seconds = 60
        mock_settings.redis_url = "redis://localhost:6379/0"
        mock_settings.trust_proxy_headers = True

        with patch("dmo.middleware.rate_limit.get_cache", fake_get_cache):
            middleware = RateLimiterMiddleware(app=lambda s, r, e: None)
            request = Request({
                "type": "http",
                "method": "GET",
                "path": "/search",
                "query_string": b"",
                "headers": [],
                "server": ("localhost", 8000),
            })

            async def fake_call_next(req):
                from starlette.responses import Response
                return Response()

            with pytest.raises(HTTPException) as exc_info:
                await middleware.dispatch(request, fake_call_next)

            assert exc_info.value.status_code == 429
            assert "Rate limit exceeded" in exc_info.value.detail


@pytest.mark.asyncio
async def test_rate_limit_redis_error_passes_through(client: AsyncClient, session):
    import redis.asyncio as redis_asyncio

    with patch("dmo.middleware.rate_limit.settings") as mock_settings:
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_max_requests = 100
        mock_settings.rate_limit_window_seconds = 60
        mock_settings.redis_url = "redis://localhost:6379/0"

        fake_get_cache = AsyncMock(side_effect=redis_asyncio.ConnectionError)
        with patch("dmo.middleware.rate_limit.get_cache", fake_get_cache):
            resp = await client.get("/search")
            assert resp.status_code == 200
            assert "X-RateLimit-Limit" not in resp.headers


def test_get_client_ip_from_forwarded_for():
    """Verify client IP is extracted from X-Forwarded-For when trust_proxy_headers=True."""
    from dmo.middleware.rate_limit import RateLimiterMiddleware

    forwarded_ip = "10.0.0.1"
    request = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers = {"X-Forwarded-For": f"{forwarded_ip}, 192.168.1.1"}

    with patch("dmo.middleware.rate_limit.settings") as mock_settings:
        mock_settings.trust_proxy_headers = True

        middleware = RateLimiterMiddleware(app=MagicMock())
        result = middleware._get_client_ip(request)
        assert result == forwarded_ip


def test_get_client_ip_direct_connection():
    """Verify client IP from request.client.host when no X-Forwarded-For."""
    from dmo.middleware.rate_limit import RateLimiterMiddleware

    request = MagicMock()
    request.client.host = "10.0.0.1"
    request.headers = {}

    with patch("dmo.middleware.rate_limit.settings") as mock_settings:
        mock_settings.trust_proxy_headers = True

        middleware = RateLimiterMiddleware(app=MagicMock())
        result = middleware._get_client_ip(request)
        assert result == "10.0.0.1"


def test_get_client_ip_proxy_disabled():
    """Verify direct IP when trust_proxy_headers=False."""
    from dmo.middleware.rate_limit import RateLimiterMiddleware

    request = MagicMock()
    request.client.host = "10.0.0.1"
    request.headers = {"X-Forwarded-For": "1.2.3.4"}

    with patch("dmo.middleware.rate_limit.settings") as mock_settings:
        mock_settings.trust_proxy_headers = False

        middleware = RateLimiterMiddleware(app=MagicMock())
        result = middleware._get_client_ip(request)
        assert result == "10.0.0.1"


def test_rate_limit_per_ip_isolation():
    """Verify two different IPs have separate rate limit keys."""
    from dmo.middleware.rate_limit import RateLimiterMiddleware

    with patch("dmo.middleware.rate_limit.settings") as mock_settings:
        mock_settings.trust_proxy_headers = True

        middleware = RateLimiterMiddleware(app=MagicMock())

        request1 = MagicMock()
        request1.client.host = "10.0.0.1"
        request1.headers = {}

        request2 = MagicMock()
        request2.client.host = "10.0.0.1"
        request2.headers = {"X-Forwarded-For": "1.2.3.4"}

        ip1 = middleware._get_client_ip(request1)
        ip2 = middleware._get_client_ip(request2)

        assert ip1 == "10.0.0.1"
        assert ip2 == "1.2.3.4"
        assert ip1 != ip2


def test_get_client_ip_no_client_and_no_forwarded():
    """Verify fallback when both client and X-Forwarded-For are missing."""
    from dmo.middleware.rate_limit import RateLimiterMiddleware

    request = MagicMock()
    request.client = None
    request.headers = {}

    with patch("dmo.middleware.rate_limit.settings") as mock_settings:
        mock_settings.trust_proxy_headers = True

        middleware = RateLimiterMiddleware(app=MagicMock())
        result = middleware._get_client_ip(request)
        assert result == "unknown"
