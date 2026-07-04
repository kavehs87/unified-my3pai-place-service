from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.config import settings
from dmo.models.database import Entity
from dmo.services.source_filter import (
    get_disabled_sources,
    invalidate_cache,
    is_source_disabled,
    source_not_in_clause,
)

WRITE_HEADERS = {"X-API-Key": settings.api_key}


@pytest.mark.asyncio
async def test_source_filter_initially_empty(session: AsyncSession):
    """No sources are disabled by default."""
    invalidate_cache()
    disabled = await get_disabled_sources(session)
    assert disabled == set()


@pytest.mark.asyncio
async def test_source_filter_disable_via_db(session: AsyncSession):
    """Disabling a source in the DB excludes it from the disabled set."""
    invalidate_cache()

    await session.execute(
        text(
            "INSERT INTO data_sources (source, is_enabled) VALUES ('rexby', FALSE) "
            "ON CONFLICT (source) DO UPDATE SET is_enabled = FALSE"
        )
    )
    await session.commit()

    invalidate_cache()
    disabled = await get_disabled_sources(session)
    assert "rexby" in disabled

    await session.execute(text("DELETE FROM data_sources WHERE source = 'rexby'"))
    await session.commit()
    invalidate_cache()


@pytest.mark.asyncio
async def test_source_not_in_clause_empty(session: AsyncSession):
    """Returns empty clause when no sources are disabled."""
    invalidate_cache()
    await get_disabled_sources(session)
    sql, params = source_not_in_clause()
    assert sql == ""
    assert params == {}


@pytest.mark.asyncio
async def test_source_not_in_clause_populated(session: AsyncSession):
    """Returns NOT IN clause when sources are disabled."""
    invalidate_cache()

    await session.execute(
        text(
            "INSERT INTO data_sources (source, is_enabled) VALUES ('rexby', FALSE), ('disabled_src', FALSE) "
            "ON CONFLICT (source) DO UPDATE SET is_enabled = FALSE"
        )
    )
    await session.commit()

    invalidate_cache()
    await get_disabled_sources(session)
    sql, params = source_not_in_clause()
    assert "NOT IN" in sql
    assert len(params) == 2
    assert "rexby" in params.values()
    assert "disabled_src" in params.values()

    await session.execute(
        text("DELETE FROM data_sources WHERE source IN ('rexby', 'disabled_src')")
    )
    await session.commit()
    invalidate_cache()


@pytest.mark.asyncio
async def test_is_source_disabled_check(session: AsyncSession):
    """is_source_disabled returns correct status."""
    invalidate_cache()

    await session.execute(
        text(
            "INSERT INTO data_sources (source, is_enabled) VALUES ('rexby', FALSE) "
            "ON CONFLICT (source) DO UPDATE SET is_enabled = FALSE"
        )
    )
    await session.commit()

    invalidate_cache()
    await get_disabled_sources(session)
    assert is_source_disabled("rexby") is True
    assert is_source_disabled("other") is False

    await session.execute(text("DELETE FROM data_sources WHERE source = 'rexby'"))
    await session.commit()
    invalidate_cache()


@pytest.mark.asyncio
async def test_search_excludes_disabled_source(client: AsyncClient, session: AsyncSession):
    """Disabled source entities don't appear in search results."""
    invalidate_cache()

    await session.execute(
        text(
            "INSERT INTO data_sources (source, is_enabled) VALUES ('rexby', FALSE) "
            "ON CONFLICT (source) DO UPDATE SET is_enabled = FALSE"
        )
    )
    await session.commit()

    entity_enabled = Entity(
        id=uuid4(),
        source="test",
        source_id="ds-1",
        name="Enabled POI",
        place_type="poi",
    )
    entity_disabled = Entity(
        id=uuid4(),
        source="rexby",
        source_id="ds-2",
        name="Disabled POI",
        place_type="poi",
    )
    session.add(entity_enabled)
    session.add(entity_disabled)
    await session.commit()

    invalidate_cache()
    resp = await client.get("/search")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["name"] == "Enabled POI"

    await session.execute(text("DELETE FROM data_sources WHERE source = 'rexby'"))
    await session.commit()
    invalidate_cache()


@pytest.mark.asyncio
async def test_nearby_excludes_disabled_source(client: AsyncClient, session: AsyncSession):
    """Disabled source entities don't appear in nearby results."""
    invalidate_cache()

    await session.execute(
        text(
            "INSERT INTO data_sources (source, is_enabled) VALUES ('rexby', FALSE) "
            "ON CONFLICT (source) DO UPDATE SET is_enabled = FALSE"
        )
    )
    await session.commit()

    enabled_id = uuid4()
    disabled_id = uuid4()
    entity_enabled = Entity(
        id=enabled_id,
        source="test",
        source_id="nb-1",
        name="Nearby Enabled",
        place_type="poi",
        latitude=47.0,
        longitude=8.0,
    )
    entity_disabled = Entity(
        id=disabled_id,
        source="rexby",
        source_id="nb-2",
        name="Nearby Disabled",
        place_type="poi",
        latitude=47.0,
        longitude=8.0,
    )
    session.add(entity_enabled)
    session.add(entity_disabled)
    await session.commit()

    await session.execute(
        text(
            "UPDATE entities SET location = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) WHERE id = :eid"
        ).bindparams(lat=47.0, lon=8.0, eid=enabled_id)
    )
    await session.execute(
        text(
            "UPDATE entities SET location = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) WHERE id = :eid"
        ).bindparams(lat=47.0, lon=8.0, eid=disabled_id)
    )
    await session.commit()

    invalidate_cache()
    resp = await client.get("/nearby?lat=47.0&lon=8.0&radius_km=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["name"] == "Nearby Enabled"

    await session.execute(text("DELETE FROM data_sources WHERE source = 'rexby'"))
    await session.commit()
    invalidate_cache()


@pytest.mark.asyncio
async def test_map_excludes_disabled_source(client: AsyncClient, session: AsyncSession):
    """Disabled source entities don't appear in map results."""
    invalidate_cache()

    await session.execute(
        text(
            "INSERT INTO data_sources (source, is_enabled) VALUES ('rexby', FALSE) "
            "ON CONFLICT (source) DO UPDATE SET is_enabled = FALSE"
        )
    )
    await session.commit()

    enabled_id = uuid4()
    disabled_id = uuid4()
    entity_enabled = Entity(
        id=enabled_id,
        source="test",
        source_id="map-1",
        name="Map Enabled",
        place_type="poi",
        latitude=47.0,
        longitude=8.0,
    )
    entity_disabled = Entity(
        id=disabled_id,
        source="rexby",
        source_id="map-2",
        name="Map Disabled",
        place_type="poi",
        latitude=47.0,
        longitude=8.0,
    )
    session.add(entity_enabled)
    session.add(entity_disabled)
    await session.commit()

    await session.execute(
        text(
            "UPDATE entities SET location = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) WHERE id = :eid"
        ).bindparams(lat=47.0, lon=8.0, eid=enabled_id)
    )
    await session.execute(
        text(
            "UPDATE entities SET location = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) WHERE id = :eid"
        ).bindparams(lat=47.0, lon=8.0, eid=disabled_id)
    )
    await session.commit()

    invalidate_cache()
    resp = await client.get("/map?bbox=7.9,46.9,8.1,47.1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["name"] == "Map Enabled"

    await session.execute(text("DELETE FROM data_sources WHERE source = 'rexby'"))
    await session.commit()
    invalidate_cache()


@pytest.mark.asyncio
async def test_detail_returns_404_for_disabled_source(client: AsyncClient, session: AsyncSession):
    """Detail endpoint returns 404 for entities from disabled sources."""
    invalidate_cache()

    await session.execute(
        text(
            "INSERT INTO data_sources (source, is_enabled) VALUES ('rexby', FALSE) "
            "ON CONFLICT (source) DO UPDATE SET is_enabled = FALSE"
        )
    )
    await session.commit()

    entity = Entity(
        id=uuid4(),
        source="rexby",
        source_id="detail-1",
        name="Detail Disabled",
        place_type="poi",
    )
    session.add(entity)
    await session.commit()

    invalidate_cache()
    resp = await client.get("/rexby/detail-1")
    assert resp.status_code == 404

    await session.execute(text("DELETE FROM data_sources WHERE source = 'rexby'"))
    await session.commit()
    invalidate_cache()


@pytest.mark.asyncio
async def test_detail_works_for_enabled_source(client: AsyncClient, session: AsyncSession):
    """Detail endpoint works normally for enabled sources."""
    invalidate_cache()

    entity = Entity(
        id=uuid4(),
        source="test",
        source_id="detail-ok",
        name="Detail Enabled",
        place_type="poi",
    )
    session.add(entity)
    await session.commit()

    invalidate_cache()
    resp = await client.get("/test/detail-ok")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Detail Enabled"


@pytest.mark.asyncio
async def test_write_accepted_for_disabled_source(client: AsyncClient, session: AsyncSession):
    """Create endpoint still accepts entities for disabled sources."""
    invalidate_cache()

    await session.execute(
        text(
            "INSERT INTO data_sources (source, is_enabled) VALUES ('rexby', FALSE) "
            "ON CONFLICT (source) DO UPDATE SET is_enabled = FALSE"
        )
    )
    await session.commit()

    resp = await client.post(
        "/entities",
        headers=WRITE_HEADERS,
        json={
            "source": "rexby",
            "source_id": "write-test-1",
            "name": "Write to Disabled",
            "place_type": "poi",
        },
    )
    assert resp.status_code == 201

    resp = await client.get("/search?source=rexby")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0

    await session.execute(text("DELETE FROM data_sources WHERE source = 'rexby'"))
    await session.commit()
    invalidate_cache()


@pytest.mark.asyncio
async def test_invalidate_cache_works(session: AsyncSession):
    """invalidate_cache clears the in-memory cache."""
    invalidate_cache()

    await session.execute(
        text(
            "INSERT INTO data_sources (source, is_enabled) VALUES ('rexby', FALSE) "
            "ON CONFLICT (source) DO UPDATE SET is_enabled = FALSE"
        )
    )
    await session.commit()

    invalidate_cache()
    disabled = await get_disabled_sources(session)
    assert "rexby" in disabled

    await session.execute(text("UPDATE data_sources SET is_enabled = TRUE WHERE source = 'rexby'"))
    await session.commit()

    invalidate_cache()
    disabled2 = await get_disabled_sources(session)
    assert "rexby" not in disabled2

    await session.execute(text("DELETE FROM data_sources WHERE source = 'rexby'"))
    await session.commit()
    invalidate_cache()
