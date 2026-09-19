"""MCP endpoint tests: protocol, tools, validation, shaping, middleware.

Tools open their own pooled sessions (``db.read_session``), so fixture data is
committed before tool calls — the ``client`` fixture's ``get_session`` override
does not reach them.
"""

import uuid
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.config import settings
from dmo.models.database import Classification, Entity, Media

RPC_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}

EXPECTED_TOOLS = {
    "search_places",
    "find_nearby",
    "map_bounding_box",
    "get_place",
    "list_unified_categories",
    "list_classifications",
    "list_classification_categories",
}


async def rpc(
    client: AsyncClient,
    method: str,
    params: dict | None = None,
    request_id: int = 1,
    headers: dict | None = None,
):
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return await client.post("/mcp", json=body, headers={**RPC_HEADERS, **(headers or {})})


async def call_tool(client: AsyncClient, name: str, arguments: dict | None = None) -> dict:
    resp = await rpc(client, "tools/call", {"name": name, "arguments": arguments or {}})
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


def _make_entity(
    source: str = "test",
    source_id: str | None = None,
    name: str = "Test POI",
    place_type: str = "poi",
    lat: float | None = None,
    lon: float | None = None,
    **kwargs,
) -> Entity:
    return Entity(
        id=uuid4(),
        source=source,
        source_id=source_id or uuid4().hex,
        name=name,
        place_type=place_type,
        latitude=lat,
        longitude=lon,
        **kwargs,
    )


async def _insert(session: AsyncSession, entity: Entity) -> Entity:
    """Insert an entity (committed, with PostGIS location when coordinates exist)."""
    entity_id = entity.id
    lat, lon = entity.latitude, entity.longitude
    session.add(entity)
    await session.commit()
    if lat is not None and lon is not None:
        await session.execute(
            text(
                "UPDATE entities SET location = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) "
                "WHERE id = :id"
            ).bindparams(lat=lat, lon=lon, id=entity_id)
        )
        await session.commit()
    await session.refresh(entity)
    return entity


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_and_tools_list(mcp_client: AsyncClient):
    resp = await rpc(
        mcp_client,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1.0"},
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    result = resp.json()["result"]
    assert result["serverInfo"]["name"] == "dmo-places"

    resp = await rpc(mcp_client, "tools/list")
    assert resp.status_code == 200
    tools = resp.json()["result"]["tools"]
    assert {t["name"] for t in tools} == EXPECTED_TOOLS
    assert len(tools) == 7
    for tool in tools:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
        assert tool["annotations"]["idempotentHint"] is True
        assert tool["annotations"]["openWorldHint"] is False
        assert tool["inputSchema"]["type"] == "object"


@pytest.mark.asyncio
async def test_path_and_method_regression(mcp_client: AsyncClient):
    resp = await rpc(mcp_client, "tools/list")
    assert resp.status_code == 200

    # GET /mcp/mcp falls through to the REST catch-all `/{source}/{source_id}`
    # (no entity named mcp/mcp) and POST has no matching method → 405.
    resp = await mcp_client.get("/mcp/mcp")
    assert resp.status_code == 404
    resp = await mcp_client.post("/mcp/mcp", json={"jsonrpc": "2.0", "id": 1}, headers=RPC_HEADERS)
    assert resp.status_code == 405

    resp = await mcp_client.get(
        "/mcp",
        headers={
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2026-07-28",
        },
    )
    assert resp.status_code == 405

    resp = await mcp_client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1}, headers=RPC_HEADERS)
    assert resp.status_code == 307


@pytest.mark.asyncio
async def test_transport_security(mcp_client: AsyncClient):
    resp = await rpc(mcp_client, "tools/list", headers={"Host": "evil.example.com"})
    assert resp.status_code == 421

    resp = await mcp_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Content-Type": "text/plain", "Accept": "application/json"},
    )
    assert resp.status_code == 400

    resp = await mcp_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Content-Type": "application/json", "Accept": "text/plain"},
    )
    assert resp.status_code == 406


@pytest.mark.asyncio
async def test_middleware_applies_to_mcp(mcp_client: AsyncClient):
    resp = await rpc(mcp_client, "tools/list")
    assert resp.status_code == 200
    assert uuid.UUID(resp.headers["X-Request-ID"])

    client_id = str(uuid4())
    resp = await rpc(mcp_client, "tools/list", headers={"X-Request-ID": client_id})
    assert resp.headers["X-Request-ID"] == client_id


@pytest.mark.asyncio
async def test_rate_limit_middleware_applies(mcp_client: AsyncClient):
    from unittest.mock import AsyncMock, patch

    class FakePipeline:
        def __init__(self, count: int):
            self.count = count
            self.cmds: list[str] = []

        def zremrangebyscore(self, *a):
            self.cmds.append("zremrangebyscore")
            return self

        def zadd(self, *a):
            self.cmds.append("zadd")
            return self

        def zcard(self, *a):
            self.cmds.append("zcard")
            return self

        def expire(self, *a):
            self.cmds.append("expire")
            return self

        async def execute(self):
            if "zcard" in self.cmds:
                return [0, self.count, True]
            return [True, True]

    class FakeRedis:
        def pipeline(self):
            return FakePipeline(count=1)

    with patch("dmo.middleware.rate_limit.settings") as mock_settings:
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_max_requests = 100
        mock_settings.rate_limit_window_seconds = 60
        mock_settings.redis_url = "redis://localhost:6379/0"
        mock_settings.trust_proxy_headers = False

        with patch("dmo.middleware.rate_limit.get_cache", AsyncMock(return_value=FakeRedis())):
            resp = await rpc(mcp_client, "tools/list")
            assert resp.status_code == 200
            assert resp.headers.get("X-RateLimit-Limit") == "100"


@pytest.mark.asyncio
async def test_no_write_tools_or_write_imports(mcp_client: AsyncClient):
    import inspect

    import dmo.mcp.auth
    import dmo.mcp.server
    import dmo.mcp.shaping
    import dmo.mcp.tools

    # Source-level guard: no write-service imports anywhere under dmo/mcp.
    for module in (
        dmo.mcp.auth,
        dmo.mcp.server,
        dmo.mcp.shaping,
        dmo.mcp.tools,
    ):
        source = inspect.getsource(module)
        assert "dmo.services.write" not in source, module.__name__
        assert "write_session" not in source, module.__name__

    resp = await rpc(mcp_client, "tools/list")
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert not any(n.startswith(("create_", "update_", "delete_")) for n in names)


# ---------------------------------------------------------------------------
# search_places
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_places_happy_path_and_filters(mcp_client: AsyncClient, session: AsyncSession):
    await _insert(session, _make_entity(source="alpha", name="Mountain Hut Alpha"))
    await _insert(
        session,
        _make_entity(source="beta", name="Mountain Hut Beta", place_type="restaurant"),
    )

    result = await call_tool(mcp_client, "search_places", {"q": "Mountain Hut"})
    assert result["isError"] is False
    assert result["structuredContent"]["total"] == 2
    first = result["structuredContent"]["results"][0]
    assert "attributes" not in first

    result = await call_tool(mcp_client, "search_places", {"q": "Mountain Hut", "source": "alpha"})
    assert result["structuredContent"]["total"] == 1
    assert result["structuredContent"]["results"][0]["source"] == "alpha"

    result = await call_tool(
        mcp_client, "search_places", {"q": "Mountain Hut", "place_type": "restaurant"}
    )
    assert result["structuredContent"]["total"] == 1
    assert result["structuredContent"]["results"][0]["source"] == "beta"


@pytest.mark.asyncio
async def test_search_places_both_or_neither_coordinates(mcp_client: AsyncClient):
    result = await call_tool(mcp_client, "search_places", {"q": "x", "lat": 47.0})
    assert result["isError"] is True
    assert "lat and lon" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_search_places_unknown_category_matches_rest(
    client: AsyncClient, mcp_client: AsyncClient, session: AsyncSession
):
    await _insert(session, _make_entity(name="Categoryless Place"))

    mcp_result = await call_tool(
        mcp_client, "search_places", {"q": "Categoryless", "unified_category": "no-such-slug"}
    )
    rest_resp = await client.get("/search?q=Categoryless&unified_category=no-such-slug")
    assert mcp_result["structuredContent"]["total"] == rest_resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_search_places_include_attributes(mcp_client: AsyncClient, session: AsyncSession):
    await _insert(
        session,
        _make_entity(name="Attributed Place", attributes={"stars": 4, "secret": "x"}),
    )

    default = await call_tool(mcp_client, "search_places", {"q": "Attributed Place"})
    assert "attributes" not in default["structuredContent"]["results"][0]

    with_attrs = await call_tool(
        mcp_client,
        "search_places",
        {"q": "Attributed Place", "include_attributes": True},
    )
    assert with_attrs["structuredContent"]["results"][0]["attributes"] == {
        "stars": 4,
        "secret": "x",
    }


# ---------------------------------------------------------------------------
# find_nearby / map_bounding_box
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_nearby_ordering_and_radius_cap(mcp_client: AsyncClient, session: AsyncSession):
    await _insert(session, _make_entity(name="Far POI", lat=46.99, lon=7.45))
    await _insert(session, _make_entity(name="Close POI", lat=46.951, lon=7.451))

    result = await call_tool(
        mcp_client, "find_nearby", {"lat": 46.95, "lon": 7.45, "radius_km": 10}
    )
    assert result["isError"] is False
    results = result["structuredContent"]["results"]
    assert [r["name"] for r in results] == ["Close POI", "Far POI"]
    assert results[0]["distance_km"] <= results[1]["distance_km"]

    result = await call_tool(
        mcp_client, "find_nearby", {"lat": 46.95, "lon": 7.45, "radius_km": 501}
    )
    assert result["isError"] is True
    assert "radius_km" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_map_bounding_box_validation_and_results(
    mcp_client: AsyncClient, session: AsyncSession
):
    await _insert(session, _make_entity(name="Inside POI", lat=46.95, lon=7.45))

    result = await call_tool(
        mcp_client,
        "map_bounding_box",
        {"min_lon": 7.4, "min_lat": 46.9, "max_lon": 7.5, "max_lat": 47.0},
    )
    assert result["isError"] is False
    assert result["structuredContent"]["total"] == 1

    result = await call_tool(
        mcp_client,
        "map_bounding_box",
        {"min_lon": 7.5, "min_lat": 46.9, "max_lon": 7.4, "max_lat": 47.0},
    )
    assert result["isError"] is True
    assert "min must be less than max" in result["content"][0]["text"]

    result = await call_tool(
        mcp_client,
        "map_bounding_box",
        {"min_lon": -200.0, "min_lat": 46.9, "max_lon": 7.5, "max_lat": 47.0},
    )
    assert result["isError"] is True
    assert "coordinate range" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# get_place
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_place_not_found(mcp_client: AsyncClient):
    result = await call_tool(mcp_client, "get_place", {"source": "nope", "source_id": "404"})
    assert result["isError"] is True
    assert "not found" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_get_place_truncation_and_open_status(mcp_client: AsyncClient, session: AsyncSession):
    entity = await _insert(
        session,
        _make_entity(
            name="Big Detail POI",
            lat=46.95,
            lon=7.45,
            is_open=True,
            attributes={"a": 1},
        ),
    )
    entity_id, source, source_id = entity.id, entity.source, entity.source_id
    for i in range(25):
        session.add(Media(entity_id=entity_id, url=f"https://example.com/{i}.jpg"))
    for i in range(60):
        session.add(Classification(entity_id=entity_id, category="kind", value_code=f"code-{i}"))
    await session.commit()

    result = await call_tool(mcp_client, "get_place", {"source": source, "source_id": source_id})
    assert result["isError"] is False
    data = result["structuredContent"]
    assert data["media_total"] == 25
    assert len(data["media"]) == settings.mcp_detail_max_media
    assert data["classifications_total"] == 60
    assert len(data["classifications"]) == settings.mcp_detail_max_classifications
    assert data["attributes"] == {"a": 1}
    assert "is_open" in data and "opens_at" in data and "closes_at" in data


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pagination_cursor_round_trip(mcp_client: AsyncClient, session: AsyncSession):
    for i in range(3):
        await _insert(session, _make_entity(name=f"Cursor POI {i}"))

    first = await call_tool(mcp_client, "search_places", {"q": "Cursor POI", "page_size": 2})
    page1 = first["structuredContent"]
    assert page1["total"] == 3
    assert len(page1["results"]) == 2
    assert page1["has_more"] is True
    assert page1["next_cursor"]

    second = await call_tool(
        mcp_client,
        "search_places",
        {"q": "Cursor POI", "page_size": 2, "cursor": page1["next_cursor"]},
    )
    page2 = second["structuredContent"]
    assert len(page2["results"]) == 1
    assert page2["has_more"] is False

    names = {r["name"] for r in page1["results"]} | {r["name"] for r in page2["results"]}
    assert names == {"Cursor POI 0", "Cursor POI 1", "Cursor POI 2"}


@pytest.mark.asyncio
async def test_mode_mismatched_cursor_is_error(mcp_client: AsyncClient, session: AsyncSession):
    for i in range(3):
        await _insert(session, _make_entity(name=f"Mismatch POI {i}"))

    # No query → name-mode cursor; replaying it on a ranked (q) search is a mismatch.
    first = await call_tool(mcp_client, "search_places", {"page_size": 1})
    cursor = first["structuredContent"]["next_cursor"]
    assert cursor

    result = await call_tool(
        mcp_client,
        "search_places",
        {"q": "Mismatch POI", "cursor": cursor},
    )
    assert result["isError"] is True
    assert "cursor" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_page_size_is_capped(mcp_client: AsyncClient, session: AsyncSession):
    for i in range(settings.mcp_max_page_size + 10):
        await _insert(session, _make_entity(name=f"Cap POI {i}"))

    result = await call_tool(mcp_client, "search_places", {"q": "Cap POI", "page_size": 999})
    assert len(result["structuredContent"]["results"]) == settings.mcp_max_page_size


# ---------------------------------------------------------------------------
# list tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_unified_categories(mcp_client: AsyncClient, session: AsyncSession):
    from dmo.models.database import UnifiedCategory

    parent = UnifiedCategory(slug=f"test_mcp_{uuid4().hex[:8]}", name="Test Parent")
    slug = parent.slug
    session.add(parent)
    await session.commit()

    result = await call_tool(mcp_client, "list_unified_categories")
    assert result["isError"] is False
    slugs = [c["slug"] for c in result["structuredContent"]["categories"]]
    assert slug in slugs


@pytest.mark.asyncio
async def test_list_classifications_and_categories(mcp_client: AsyncClient, session: AsyncSession):
    entity = await _insert(session, _make_entity(name="Classified POI"))
    entity_id = entity.id
    session.add(Classification(entity_id=entity_id, category="kind", value_code="tower"))
    await session.commit()

    result = await call_tool(mcp_client, "list_classifications", {"entity_id": str(entity_id)})
    assert result["isError"] is False
    assert result["structuredContent"]["total"] == 1
    assert result["structuredContent"]["results"][0]["value_code"] == "tower"

    cats = await call_tool(mcp_client, "list_classification_categories")
    assert cats["isError"] is False
    assert "kind" in cats["structuredContent"]["categories"]


@pytest.mark.asyncio
async def test_tool_error_shape_carries_call_id(mcp_client: AsyncClient):
    result = await call_tool(mcp_client, "get_place", {"source": "x", "source_id": "y"})
    assert result["isError"] is True
    assert "(call " in result["content"][0]["text"]
