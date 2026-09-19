import os

# Must set env before any dmo imports (mirrors other admin-script tests)
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DB_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/dmo"
)

import pytest
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.admin_scripts.country_data import ISO2_NAMES, resolve_country
from dmo.admin_scripts.heal_missing_address import build_address
from dmo.admin_scripts.registry import get_script
from dmo.models.database import Classification, Entity, Media, UnifiedCategory

NEW_SCRIPTS = (
    "unify_opentripmap_categories",
    "backfill_summary_from_description",
    "backfill_thumbnail_from_media",
    "backfill_classifications_from_kinds",
    "backfill_source_url",
    "heal_missing_address",
    "normalize_countries",
    "score_entities",
)


# ─── Unit: country resolution ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("United Kingdom", "GB"),
        ("Deutschland", "DE"),
        ("España", "ES"),
        ("Türkiye", "TR"),
        ("Česká republika", "CZ"),
        ("日本 (Japan)", "JP"),
        ("Ελλάδα", "GR"),
        ("Bahamas, The", "BS"),
        ("China 中国", "CN"),
        ("Korea, South", "KR"),
        ("België - Belgique - Belgien", "BE"),
        ("ch", "CH"),
        ("USA", "US"),
        ("Bildstock", None),
        ("n.v.", None),
        ("??", None),
        ("", None),
        (None, None),
    ],
)
def test_resolve_country(raw, expected):
    assert resolve_country(raw) == expected


def test_iso2_names_cover_common_countries():
    for code in ("CH", "DE", "FR", "GB", "US", "JP", "IT", "ES"):
        assert code in ISO2_NAMES


# ─── Unit: address builder ───────────────────────────────────────────────────


def test_build_address_expands_iso2_and_skips_empty():
    assert build_address("Zurich", "Zurich", "CH") == "Zurich, Zurich, Switzerland"
    assert build_address(None, "Bavaria", "DE") == "Bavaria, Germany"
    assert build_address("Paris", None, None) == "Paris"
    assert build_address("", "  ", None) is None
    assert build_address(None, None, None) is None
    assert build_address("X", "Y", "ZZ") == "X, Y, ZZ"


# ─── Registry ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_data_quality_scripts_registered():
    for name in NEW_SCRIPTS:
        assert get_script(name) is not None, f"{name} not discovered"


# ─── DB integration ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backfill_summary_from_description(session: AsyncSession):
    entity = Entity(
        source="test_dq_sum",
        source_id="1",
        name="Summary Test",
        place_type="poi",
        description="A long description that should become the summary.",
        summary=None,
    )
    session.add(entity)
    await session.flush()
    eid = entity.id
    await session.commit()

    script = get_script("backfill_summary_from_description")
    dry = await script.run({"source": "test_dq_sum", "dry_run": True}, db=None)
    assert dry.affected_count == 1

    live = await script.run({"source": "test_dq_sum", "dry_run": False, "batch_size": 100}, db=None)
    assert live.affected_count == 1

    summary = (await session.exec(select(Entity.summary).where(Entity.id == eid))).first()
    assert summary == "A long description that should become the summary."


@pytest.mark.asyncio
async def test_backfill_classifications_from_kinds(session: AsyncSession):
    entity = Entity(
        source="test_dq_kinds",
        source_id="1",
        name="Kind Test",
        place_type="poi",
        secondary_types=["museum", "interesting_places"],
    )
    session.add(entity)
    await session.flush()
    eid = entity.id
    await session.commit()

    script = get_script("backfill_classifications_from_kinds")
    live = await script.run(
        {"source": "test_dq_kinds", "dry_run": False, "batch_size": 100}, db=None
    )
    assert live.affected_count == 1

    rows = (await session.exec(select(Classification).where(Classification.entity_id == eid))).all()
    assert {r.value_code for r in rows} == {"museum"}  # generic kind skipped
    assert rows[0].category == "kind"
    assert rows[0].value_title == "Museum"


@pytest.mark.asyncio
async def test_backfill_thumbnail_from_media(session: AsyncSession):
    entity = Entity(source="test_dq_thumb", source_id="1", name="Thumb Test", place_type="poi")
    session.add(entity)
    await session.flush()
    eid = entity.id
    session.add(
        Media(
            entity_id=eid,
            media_type="image",
            url="https://example.com/first.jpg",
            sort_order=1,
            is_active=True,
        )
    )
    await session.commit()

    script = get_script("backfill_thumbnail_from_media")
    live = await script.run(
        {"source": "test_dq_thumb", "dry_run": False, "batch_size": 100}, db=None
    )
    assert live.affected_count == 1

    thumb = (await session.exec(select(Entity.thumbnail_url).where(Entity.id == eid))).first()
    assert thumb == "https://example.com/first.jpg"


@pytest.mark.asyncio
async def test_heal_missing_address(session: AsyncSession):
    entity = Entity(
        source="test_dq_addr",
        source_id="1",
        name="Address Test",
        place_type="poi",
        locality="Zurich",
        country="CH",
    )
    session.add(entity)
    await session.flush()
    eid = entity.id
    await session.commit()

    script = get_script("heal_missing_address")
    live = await script.run(
        {"source": "test_dq_addr", "dry_run": False, "batch_size": 100}, db=None
    )
    assert live.affected_count == 1

    address = (await session.exec(select(Entity.address).where(Entity.id == eid))).first()
    assert address == "Zurich, Switzerland"


@pytest.mark.asyncio
async def test_backfill_source_url(session: AsyncSession):
    entity = Entity(
        source="test_dq_url",
        source_id="1",
        name="URL Test",
        place_type="poi",
        attributes={"sm_url": "https://example.com/route/1"},
    )
    session.add(entity)
    await session.flush()
    eid = entity.id
    await session.commit()

    script = get_script("backfill_source_url")
    live = await script.run({"source": "test_dq_url", "dry_run": False, "batch_size": 100}, db=None)
    assert live.affected_count == 1

    url = (await session.exec(select(Entity.source_url).where(Entity.id == eid))).first()
    assert url == "https://example.com/route/1"


@pytest.mark.asyncio
async def test_normalize_countries(session: AsyncSession):
    good = Entity(
        source="test_dq_country",
        source_id="1",
        name="Country Good",
        place_type="poi",
        country="Deutschland",
    )
    junk = Entity(
        source="test_dq_country",
        source_id="2",
        name="Country Junk",
        place_type="poi",
        country="Bildstock",
    )
    session.add(good)
    session.add(junk)
    await session.flush()
    good_id, junk_id = good.id, junk.id
    await session.commit()

    script = get_script("normalize_countries")
    live = await script.run(
        {"source": "test_dq_country", "dry_run": False, "batch_size": 100}, db=None
    )
    assert live.affected_count == 2

    assert (await session.exec(select(Entity.country).where(Entity.id == good_id))).first() == "DE"
    assert (await session.exec(select(Entity.country).where(Entity.id == junk_id))).first() is None


@pytest.mark.asyncio
async def test_score_entities_formula_wiring(session: AsyncSession):
    entity = Entity(
        source="test_dq_score",
        source_id="1",
        name="Scored Place",
        place_type="poi",
    )
    session.add(entity)
    await session.flush()
    eid = entity.id
    await session.commit()

    script = get_script("score_entities")
    live = await script.run(
        {"source": "test_dq_score", "dry_run": False, "batch_size": 100}, db=None
    )
    assert live.affected_count == 1

    score = (await session.exec(select(Entity.quality_score).where(Entity.id == eid))).first()
    # only the meaningful-name component applies: 2 points
    assert score == 2


@pytest.mark.asyncio
async def test_unify_opentripmap_categories_priority(session: AsyncSession):
    top = UnifiedCategory(slug="test_dq_attraction", name="Test DQ Attraction", sort_order=1)
    session.add(top)
    await session.flush()
    leaf_high = UnifiedCategory(
        slug="test_dq_leaf_high", name="Test DQ High", parent_id=top.id, sort_order=1
    )
    leaf_low = UnifiedCategory(
        slug="test_dq_leaf_low", name="Test DQ Low", parent_id=top.id, sort_order=2
    )
    session.add(leaf_high)
    session.add(leaf_low)
    await session.flush()

    await session.exec(
        text(
            "INSERT INTO place_kind_mappings (source, kind, unified_category_id, priority)"
            " VALUES ('test_dq_otm', 'kind_low', :low, 10),"
            "        ('test_dq_otm', 'kind_high', :high, 90)"
        ).bindparams(low=leaf_low.id, high=leaf_high.id)
    )

    entity = Entity(
        source="test_dq_otm",
        source_id="1",
        name="Kind Priority Test",
        place_type="poi",
        secondary_types=["kind_low", "kind_high"],
    )
    session.add(entity)
    await session.flush()
    eid = entity.id
    await session.commit()

    script = get_script("unify_opentripmap_categories")
    live = await script.run({"source": "test_dq_otm", "dry_run": False, "batch_size": 100}, db=None)
    assert live.affected_count == 1

    row = (
        await session.exec(
            select(Entity.unified_category, Entity.unified_subcategory).where(Entity.id == eid)
        )
    ).first()
    assert row == ("test_dq_attraction", "test_dq_leaf_high")
