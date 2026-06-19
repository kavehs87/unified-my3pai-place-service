"""Gzip compression tests — standalone, no DB required.

Tests verify GZipMiddleware behavior using raw ASGI calls.
These run anywhere without PostGIS/Redis dependencies.
"""
import gzip
import json

import pytest
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware


def _build_app():
    """Build minimal FastAPI app with GZipMiddleware."""
    app = FastAPI()
    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.router.route("/small", methods=["GET"], endpoint=lambda: {"status": "ok"})
    app.router.route(
        "/large",
        methods=["GET"],
        endpoint=lambda: {"items": [{"name": f"Entity {i}", "desc": "x" * 200} for i in range(10)]},
    )
    return app


async def _asgi_request(app, path, headers=None):
    """Make raw ASGI request — bypasses httpx auto-decompression."""
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": [(k.encode(), v.encode()) for k, v in (headers or [])],
        "server": ("test", 80),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    state = {"headers": [], "body": b""}

    async def send(msg):
        if msg["type"] == "http.response.start":
            state["headers"].extend(msg.get("headers", []))
        elif msg["type"] == "http.response.body":
            state["body"] += msg.get("body", b"")

    await app(scope, receive, send)
    return dict(state["headers"]), state["body"]


@pytest.mark.asyncio
async def test_gzip_vary_header_present():
    """Vary: Accept-Encoding proves GZipMiddleware is active."""
    app = _build_app()
    headers, _ = await _asgi_request(app, "/large", [("Accept-Encoding", "gzip")])
    vary = headers.get(b"vary", b"").decode()
    assert "Accept-Encoding" in vary


@pytest.mark.asyncio
async def test_gzip_small_response_not_compressed():
    """Small responses (<500 bytes) skip compression."""
    app = _build_app()
    headers, body = await _asgi_request(app, "/small", [("Accept-Encoding", "gzip")])
    assert headers.get(b"content-encoding") is None
    data = json.loads(body)
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_gzip_large_response_compressed():
    """Large responses are gzip compressed and decompress to valid JSON."""
    app = _build_app()
    headers, body = await _asgi_request(app, "/large", [("Accept-Encoding", "gzip")])
    assert headers.get(b"content-encoding") == b"gzip"
    vary = headers.get(b"vary", b"").decode()
    assert "Accept-Encoding" in vary
    decompressed = gzip.decompress(body)
    data = json.loads(decompressed)
    assert len(data["items"]) == 10
    assert data["items"][0]["name"] == "Entity 0"


@pytest.mark.asyncio
async def test_gzip_no_accept_encoding():
    """Without Accept-Encoding header, responses are uncompressed."""
    app = _build_app()
    headers, body = await _asgi_request(app, "/large")
    assert headers.get(b"content-encoding") is None
    data = json.loads(body)
    assert len(data["items"]) == 10


@pytest.mark.asyncio
async def test_gzip_identity_encoding():
    """Accept-Encoding: identity forces no compression."""
    app = _build_app()
    headers, body = await _asgi_request(app, "/large", [("Accept-Encoding", "identity")])
    assert headers.get(b"content-encoding") is None
    data = json.loads(body)
    assert len(data["items"]) == 10


@pytest.mark.asyncio
async def test_gzip_decompressed_data_integrity():
    """Decompressed data matches expected structure exactly."""
    app = _build_app()
    headers, body = await _asgi_request(app, "/large", [("Accept-Encoding", "gzip")])
    decompressed = gzip.decompress(body)
    data = json.loads(decompressed)
    assert "items" in data
    assert len(data["items"]) == 10
    for i, item in enumerate(data["items"]):
        assert item["name"] == f"Entity {i}"
        assert item["desc"] == "x" * 200
