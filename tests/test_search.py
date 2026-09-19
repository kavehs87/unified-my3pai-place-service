from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.exceptions import AppError
from dmo.models.database import Entity
from dmo.services.pagination import encode_cursor
from dmo.services.search import search


@pytest.mark.asyncio
async def test_search_empty(client: AsyncClient):
    resp = await client.get("/search")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_search_with_data(client: AsyncClient, session: AsyncSession):
    entity = Entity(
        id=uuid4(),
        source="test",
        source_id="1",
        name="Test POI",
        place_type="poi",
        country="CH",
    )
    session.add(entity)
    await session.commit()

    resp = await client.get("/search?q=Test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["name"] == "Test POI"


@pytest.mark.asyncio
async def test_search_filter_by_source(client: AsyncClient, session: AsyncSession):
    entity = Entity(
        id=uuid4(),
        source="rexby",
        source_id="1",
        name="Rexby POI",
        place_type="hike",
    )
    session.add(entity)
    await session.commit()

    resp = await client.get("/search?source=rexby")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1

    resp2 = await client.get("/search?source=dzt")
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 0


@pytest.mark.asyncio
async def test_search_filter_by_place_type(client: AsyncClient, session: AsyncSession):
    entity = Entity(
        id=uuid4(),
        source="test",
        source_id="1",
        name="Hike Trail",
        place_type="hike",
    )
    session.add(entity)
    await session.commit()

    resp = await client.get("/search?place_type=hike")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp2 = await client.get("/search?place_type=restaurant")
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 0


@pytest.mark.asyncio
async def test_search_pagination(client: AsyncClient, session: AsyncSession):
    for i in range(5):
        entity = Entity(
            id=uuid4(),
            source="test",
            source_id=str(i),
            name=f"POI {i}",
            place_type="poi",
        )
        session.add(entity)
    await session.commit()

    resp = await client.get("/search?page_size=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["results"]) == 2
    assert data["has_more"] is True
    assert data["next_cursor"] is not None
    assert "page" not in data


@pytest.mark.asyncio
async def test_search_cursor_pagination(client: AsyncClient, session: AsyncSession):
    for i in range(5):
        entity = Entity(
            id=uuid4(),
            source="test",
            source_id=str(i),
            name=f"POI {i}",
            place_type="poi",
        )
        session.add(entity)
    await session.commit()

    resp1 = await client.get("/search?page_size=2")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["has_more"] is True
    cursor = data1["next_cursor"]

    resp2 = await client.get(f"/search?page_size=2&cursor={cursor}")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["results"]) == 2
    assert data1["results"][-1]["id"] != data2["results"][0]["id"]


@pytest.mark.asyncio
async def test_search_fulltext_flag(client: AsyncClient, session: AsyncSession):
    """Test that fulltext flag enables summary search.

    Default (fulltext=False) searches name only.
    fulltext=True searches name + summary.
    """
    entity_name = Entity(
        id=uuid4(),
        source="test",
        source_id="ft-1",
        name="Mountain Lodge",
        place_type="hotel",
        country="CH",
    )
    entity_summary = Entity(
        id=uuid4(),
        source="test",
        source_id="ft-2",
        name="Alpine Hotel",
        summary="Cozy hotel near Mountain Lodge in the Alps",
        place_type="hotel",
        country="CH",
    )
    session.add(entity_name)
    session.add(entity_summary)
    await session.commit()

    # Default: name-only search finds "Mountain Lodge"
    resp = await client.get("/search?q=Mountain+Lodge")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["name"] == "Mountain Lodge"

    # fulltext=True: summary search finds "Alpine Hotel" (summary mentions "Mountain Lodge")
    resp2 = await client.get("/search?q=Mountain+Lodge&fulltext=true")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total"] == 2


@pytest.mark.asyncio
async def test_search_ranks_exact_name_over_quality(client: AsyncClient, session: AsyncSession):
    """3a: text relevance dominates; a high quality_score cannot outrank an exact name."""
    exact = Entity(
        id=uuid4(),
        source="test",
        source_id="rank-1",
        name="Eiffel Tower",
        place_type="poi",
        quality_score=20,
    )
    partial = Entity(
        id=uuid4(),
        source="test",
        source_id="rank-2",
        name="4 level tower",
        place_type="poi",
        quality_score=99,
    )
    session.add(exact)
    session.add(partial)
    await session.commit()

    resp = await client.get("/search?q=eiffel+tower")
    assert resp.status_code == 200
    names = [r["name"] for r in resp.json()["results"]]
    assert names[0] == "Eiffel Tower"


@pytest.mark.asyncio
async def test_search_prominence_breaks_ties(client: AsyncClient, session: AsyncSession):
    """3a: with equal text relevance, higher quality_score ranks first."""
    high = Entity(
        id=uuid4(),
        source="test",
        source_id="tie-1",
        name="Twin Peaks",
        place_type="poi",
        quality_score=90,
    )
    low = Entity(
        id=uuid4(),
        source="test",
        source_id="tie-2",
        name="Twin Peaks",
        place_type="poi",
        quality_score=10,
    )
    session.add(high)
    session.add(low)
    await session.flush()
    high_id = str(high.id)
    await session.commit()

    resp = await client.get("/search?q=twin+peaks")
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["id"] == high_id


@pytest.mark.asyncio
async def test_search_ranked_cursor_pagination(client: AsyncClient, session: AsyncSession):
    """3a: rank-based cursor paginates without gaps or overlap."""
    for i in range(5):
        session.add(
            Entity(
                id=uuid4(),
                source="test",
                source_id=f"ranked-{i}",
                name=f"Ranked Place {i}",
                place_type="poi",
            )
        )
    await session.commit()

    resp1 = await client.get("/search?q=Ranked+Place&page_size=2")
    data1 = resp1.json()
    assert len(data1["results"]) == 2
    assert data1["has_more"] is True

    resp2 = await client.get(f"/search?q=Ranked+Place&page_size=2&cursor={data1['next_cursor']}")
    data2 = resp2.json()
    ids1 = {r["id"] for r in data1["results"]}
    ids2 = {r["id"] for r in data2["results"]}
    assert len(data2["results"]) == 2
    assert not (ids1 & ids2)


@pytest.mark.asyncio
async def test_search_rejects_name_cursor_for_ranked_query(session: AsyncSession):
    """3a: legacy (name-based) cursors are rejected for ranked searches, not 500."""
    legacy_cursor = encode_cursor(uuid4(), "Some Name")
    with pytest.raises(AppError) as exc:
        await search(session, q="test", cursor=legacy_cursor)
    assert exc.value.code == "InvalidCursor"


@pytest.mark.asyncio
async def test_search_bias_prefers_nearby(client: AsyncClient, session: AsyncSession):
    """3b: with equal relevance/quality, the entity near the bias point ranks first."""
    near = Entity(
        id=uuid4(), source="test", source_id="bias-1", name="Bias Cafe", place_type="cafe"
    )
    far = Entity(id=uuid4(), source="test", source_id="bias-2", name="Bias Cafe", place_type="cafe")
    session.add(near)
    session.add(far)
    await session.flush()
    near_id, far_id = near.id, far.id
    await session.exec(
        text(
            "UPDATE entities SET location = ST_SetSRID(ST_MakePoint(8.54, 47.37), 4326)::geography"
            " WHERE id = :id"
        ).bindparams(id=near_id)
    )
    await session.exec(
        text(
            "UPDATE entities SET location = ST_SetSRID(ST_MakePoint(151.2, -33.86), 4326)::geography"
            " WHERE id = :id"
        ).bindparams(id=far_id)
    )
    await session.commit()

    resp = await client.get("/search?q=Bias+Cafe&lat=47.37&lon=8.54")
    assert resp.status_code == 200
    assert resp.json()["results"][0]["id"] == str(near_id)


@pytest.mark.asyncio
async def test_search_bias_does_not_bury_exact_name(client: AsyncClient, session: AsyncSession):
    """3b: the bias is soft — an exact far-away name still beats a weak nearby match."""
    exact = Entity(
        id=uuid4(), source="test", source_id="soft-1", name="Eiffel Tower", place_type="landmark"
    )
    nearby_weak = Entity(
        id=uuid4(),
        source="test",
        source_id="soft-2",
        name="Eiffel Tower Restaurant Zurich",
        place_type="restaurant",
    )
    session.add(exact)
    session.add(nearby_weak)
    await session.flush()
    exact_id, weak_id = exact.id, nearby_weak.id
    await session.exec(
        text(
            "UPDATE entities SET location = ST_SetSRID(ST_MakePoint(2.2945, 48.8584), 4326)::geography"
            " WHERE id = :id"
        ).bindparams(id=exact_id)
    )
    await session.exec(
        text(
            "UPDATE entities SET location = ST_SetSRID(ST_MakePoint(8.54, 47.37), 4326)::geography"
            " WHERE id = :id"
        ).bindparams(id=weak_id)
    )
    await session.commit()

    resp = await client.get("/search?q=eiffel+tower&lat=47.37&lon=8.54")
    assert resp.status_code == 200
    assert resp.json()["results"][0]["id"] == str(exact_id)


@pytest.mark.asyncio
async def test_search_bias_requires_lat_and_lon_together(client: AsyncClient):
    resp = await client.get("/search?q=test&lat=47.37")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_radius_tier_prefers_local_weak_over_far_exact(
    client: AsyncClient, session: AsyncSession
):
    """3b/C: with a radius, local matches are tiered above far exact names."""
    far_exact = Entity(
        id=uuid4(), source="test", source_id="tier-1", name="Tier Cafe", place_type="cafe"
    )
    local_weak = Entity(
        id=uuid4(), source="test", source_id="tier-2", name="Tier Cafe Zurich", place_type="cafe"
    )
    session.add(far_exact)
    session.add(local_weak)
    await session.flush()
    far_id, local_id = far_exact.id, local_weak.id
    await session.exec(
        text(
            "UPDATE entities SET location = ST_SetSRID(ST_MakePoint(151.2, -33.86), 4326)::geography"
            " WHERE id = :id"
        ).bindparams(id=far_id)
    )
    await session.exec(
        text(
            "UPDATE entities SET location = ST_SetSRID(ST_MakePoint(8.54, 47.37), 4326)::geography"
            " WHERE id = :id"
        ).bindparams(id=local_id)
    )
    await session.commit()

    resp_soft = await client.get("/search?q=Tier+Cafe&lat=47.37&lon=8.54")
    assert resp_soft.json()["results"][0]["id"] == str(far_id)

    resp_tier = await client.get("/search?q=Tier+Cafe&lat=47.37&lon=8.54&bias_radius_km=10")
    assert resp_tier.json()["results"][0]["id"] == str(local_id)


@pytest.mark.asyncio
async def test_search_radius_tier_cursor_pagination(client: AsyncClient, session: AsyncSession):
    """3b/C: tiered cursor pagination walks local matches first, without overlap."""
    local_ids = []
    for i in range(3):
        entity = Entity(
            id=uuid4(),
            source="test",
            source_id=f"ring-local-{i}",
            name=f"Ring Place Local {i}",
            place_type="poi",
        )
        session.add(entity)
        await session.flush()
        local_ids.append(str(entity.id))
        await session.exec(
            text(
                "UPDATE entities SET location = ST_SetSRID(ST_MakePoint(8.54, 47.37), 4326)::geography"
                " WHERE id = :id"
            ).bindparams(id=entity.id)
        )
    for i in range(3):
        entity = Entity(
            id=uuid4(),
            source="test",
            source_id=f"ring-far-{i}",
            name=f"Ring Place Far {i}",
            place_type="poi",
        )
        session.add(entity)
        await session.flush()
        await session.exec(
            text(
                "UPDATE entities SET location = ST_SetSRID(ST_MakePoint(151.2, -33.86), 4326)::geography"
                " WHERE id = :id"
            ).bindparams(id=entity.id)
        )
    await session.commit()

    resp1 = await client.get(
        "/search?q=Ring+Place&page_size=2&lat=47.37&lon=8.54&bias_radius_km=10"
    )
    data1 = resp1.json()
    page1_ids = [r["id"] for r in data1["results"]]
    assert all(pid in local_ids for pid in page1_ids)

    resp2 = await client.get(
        f"/search?q=Ring+Place&page_size=2&lat=47.37&lon=8.54&bias_radius_km=10"
        f"&cursor={data1['next_cursor']}"
    )
    data2 = resp2.json()
    ids2 = {r["id"] for r in data2["results"]}
    assert not (set(page1_ids) & ids2)


@pytest.mark.asyncio
async def test_search_radius_tier_rejects_soft_bias_cursor(session: AsyncSession):
    """3b: cursors minted in soft-bias mode are invalid for tiered requests (and vice versa)."""
    legacy_rank_cursor = encode_cursor(uuid4(), 0.5)
    with pytest.raises(AppError):
        await search(
            session, q="test", lat=47.37, lon=8.54, bias_radius_km=10, cursor=legacy_rank_cursor
        )
