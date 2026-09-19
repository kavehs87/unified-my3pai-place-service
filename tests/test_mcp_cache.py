"""Live-cache tests: MCP and REST share cache entries; writes invalidate.

Marked ``live_cache`` so the autouse cache-disable patch is skipped and the
real Redis-backed cache is exercised (Redis must be reachable).
"""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.config import settings
from dmo.models.database import Entity
from dmo.services import cache as cache_module
from dmo.services.taxonomy import get_category_level

pytestmark = pytest.mark.live_cache

RPC_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
WRITE_HEADERS = {"X-API-Key": settings.api_key}


async def _rpc(client: AsyncClient, method: str, params: dict | None = None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return await client.post("/mcp", json=body, headers=RPC_HEADERS)


async def _call_tool(client: AsyncClient, name: str, arguments: dict | None = None) -> dict:
    resp = await _rpc(client, "tools/call", {"name": name, "arguments": arguments or {}})
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


def _make_entity(name: str, source_id: str | None = None, **kwargs) -> Entity:
    return Entity(
        id=uuid4(),
        source="test",
        source_id=source_id or uuid4().hex,
        name=name,
        place_type="poi",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_mcp_and_rest_share_search_cache_entry(
    client: AsyncClient, mcp_client: AsyncClient, session: AsyncSession
):
    entity = _make_entity(name="Shared Cache Place")
    session.add(entity)
    await session.commit()

    # MCP populates the cache with page_size=10 (MCP default).
    result = await _call_tool(mcp_client, "search_places", {"q": "Shared Cache Place"})
    assert result["isError"] is False
    assert result["structuredContent"]["total"] == 1

    # REST call with identical params (page_size=10) must be a HIT.
    resp = await client.get("/search?q=Shared+Cache+Place&page_size=10")
    assert resp.status_code == 200
    assert resp.headers["X-Cache-Status"] == "HIT"

    await cache_module.cache_delete_pattern("dmo:search:*")


@pytest.mark.asyncio
async def test_mcp_and_rest_share_detail_cache_entry(
    client: AsyncClient, mcp_client: AsyncClient, session: AsyncSession
):
    entity = _make_entity(name="Shared Detail Place", is_open=True)
    source, source_id = entity.source, entity.source_id
    session.add(entity)
    await session.commit()

    result = await _call_tool(mcp_client, "get_place", {"source": source, "source_id": source_id})
    assert result["isError"] is False

    resp = await client.get(f"/{source}/{source_id}")
    assert resp.status_code == 200
    assert "HIT" in resp.headers["X-Cache-Status"]

    await cache_module.cache_delete_pattern("dmo:detail:*")
    await cache_module.cache_delete_pattern("dmo:open_status:*")


@pytest.mark.asyncio
async def test_entity_write_invalidates_unified_categories(
    client: AsyncClient, mcp_client: AsyncClient, session: AsyncSession
):
    # Populate the taxonomy cache through the MCP surface.
    result = await _call_tool(mcp_client, "list_unified_categories")
    assert result["isError"] is False
    assert await cache_module.cache_get("unified_categories", {}) is not None

    # Entity write must clear every pattern, including unified_categories.
    payload = {
        "source": "test",
        "source_id": uuid4().hex,
        "name": "Invalidation Place",
        "place_type": "poi",
    }
    resp = await client.post("/entities", json=payload, headers=WRITE_HEADERS)
    assert resp.status_code == 201

    assert await cache_module.cache_get("unified_categories", {}) is None


@pytest.mark.asyncio
async def test_admin_taxonomy_edit_invalidates_caches_and_levels(
    client: AsyncClient, mcp_client: AsyncClient, session: AsyncSession
):
    slug = f"test_mcp_cache_{uuid4().hex[:8]}"

    # Warm both caches.
    result = await _call_tool(mcp_client, "list_unified_categories")
    assert result["isError"] is False
    assert await cache_module.cache_get("unified_categories", {}) is not None
    assert await get_category_level(session, slug) is None

    resp = await client.post(
        "/admin/taxonomy",
        data={"name": "MCP Cache Cat", "slug": slug, "sort_order": "0"},
        auth=("admin", "admin"),
    )
    assert resp.status_code == 200

    assert await cache_module.cache_get("unified_categories", {}) is None
    # Level cache was cleared too: the new slug now resolves as top-level.
    assert await get_category_level(session, slug) == "top"

    await cache_module.cache_delete_pattern("dmo:unified_categories:*")
    await cache_module.cache_delete_pattern("dmo:taxonomy_levels:*")
