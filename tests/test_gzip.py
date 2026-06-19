"""Gzip compression tests — standalone, no DB required.

Tests verify GZipMiddleware behavior using raw ASGI calls via asyncio.run()
to avoid pytest-asyncio event loop interaction issues with GZipMiddleware.
"""
import asyncio
import gzip
import json

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware


def _build_app():
    """Build minimal FastAPI app with GZipMiddleware."""
    app = FastAPI()
    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_api_route("/small", lambda: {"status": "ok"}, methods=["GET"])
    app.add_api_route(
        "/large",
        lambda: {"items": [{"name": f"Entity {i}", "desc": "x" * 200} for i in range(10)]},
        methods=["GET"],
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
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or [])],
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


def _run_asgi(app, path, headers=None):
    """Run ASGI request in a fresh event loop to avoid pytest-asyncio issues."""
    return asyncio.run(_asgi_request(app, path, headers))


def test_gzip_vary_header_present():
    """Vary: Accept-Encoding proves GZipMiddleware is active."""
    headers, _ = _run_asgi(_build_app(), "/large", [("Accept-Encoding", "gzip")])
    vary = headers.get(b"vary", b"").decode()
    assert "Accept-Encoding" in vary


def test_gzip_small_response_not_compressed():
    """Small responses (<500 bytes) skip compression."""
    headers, body = _run_asgi(_build_app(), "/small", [("Accept-Encoding", "gzip")])
    assert headers.get(b"content-encoding") is None
    data = json.loads(body)
    assert data["status"] == "ok"


def test_gzip_large_response_compressed():
    """Large responses are gzip compressed and decompress to valid JSON."""
    headers, body = _run_asgi(_build_app(), "/large", [("Accept-Encoding", "gzip")])
    assert headers.get(b"content-encoding") == b"gzip"
    vary = headers.get(b"vary", b"").decode()
    assert "Accept-Encoding" in vary
    decompressed = gzip.decompress(body)
    data = json.loads(decompressed)
    assert len(data["items"]) == 10
    assert data["items"][0]["name"] == "Entity 0"


def test_gzip_no_accept_encoding():
    """Without Accept-Encoding header, responses are uncompressed."""
    headers, body = _run_asgi(_build_app(), "/large")
    assert headers.get(b"content-encoding") is None
    data = json.loads(body)
    assert len(data["items"]) == 10


def test_gzip_identity_encoding():
    """Accept-Encoding: identity forces no compression."""
    headers, body = _run_asgi(_build_app(), "/large", [("Accept-Encoding", "identity")])
    assert headers.get(b"content-encoding") is None
    data = json.loads(body)
    assert len(data["items"]) == 10


def test_gzip_decompressed_data_integrity():
    """Decompressed data matches expected structure exactly."""
    headers, body = _run_asgi(_build_app(), "/large", [("Accept-Encoding", "gzip")])
    decompressed = gzip.decompress(body)
    data = json.loads(decompressed)
    assert "items" in data
    assert len(data["items"]) == 10
    for i, item in enumerate(data["items"]):
        assert item["name"] == f"Entity {i}"
        assert item["desc"] == "x" * 200
