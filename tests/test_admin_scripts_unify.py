import os

# Must set env before any dmo imports
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DB_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/dmo"
)

from uuid import uuid4

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.admin_scripts.registry import discover_scripts, get_script
from dmo.models.database import Entity, PlaceTypeMapping, UnifiedCategory


@pytest.mark.asyncio
async def test_scripts_discovered():
    registry = discover_scripts()
    assert "normalize_place_types" in registry
    assert "unify_place_types" in registry
    assert "extract_attributes" in registry
    assert "unify_classifications" in registry
    assert "clean_dzt_data" in registry


@pytest.mark.asyncio
async def test_unify_place_types_meta():
    script = get_script("unify_place_types")
    assert script is not None
    assert script.meta.name == "unify_place_types"
    assert script.meta.category == "Unify"
    param_names = [p.name for p in script.meta.parameters]
    assert "source" in param_names
    assert "dry_run" in param_names
    assert "batch_size" in param_names


@pytest.mark.asyncio
async def test_extract_attributes_meta():
    script = get_script("extract_attributes")
    assert script is not None
    assert script.meta.name == "extract_attributes"
    assert script.meta.category == "Fix"
    param_names = [p.name for p in script.meta.parameters]
    assert "source" in param_names
    assert "dry_run" in param_names


@pytest.mark.asyncio
async def test_unify_classifications_meta():
    script = get_script("unify_classifications")
    assert script is not None
    assert script.meta.name == "unify_classifications"
    assert script.meta.category == "Unify"
    param_names = [p.name for p in script.meta.parameters]
    assert "source" in param_names
    assert "dry_run" in param_names


@pytest.mark.asyncio
async def test_clean_dzt_data_meta():
    script = get_script("clean_dzt_data")
    assert script is not None
    assert script.meta.name == "clean_dzt_data"
    assert script.meta.category == "Heal"
    param_names = [p.name for p in script.meta.parameters]
    assert "dry_run" in param_names


@pytest.mark.asyncio
async def test_unify_place_types_dry_run(session: AsyncSession):
    # Use unique slugs to avoid conflicts with previous test-run data
    # (unified_categories table is not in session cleanup)
    top = UnifiedCategory(slug="test_unify_food", name="Test Unify Food", sort_order=1)
    session.add(top)
    await session.flush()
    leaf = UnifiedCategory(
        slug="test_unify_restaurant", name="Test Unify Restaurant", parent_id=top.id, sort_order=1
    )
    session.add(leaf)
    await session.flush()
    mapping = PlaceTypeMapping(
        source="test_source",
        source_place_type="test_restaurant",
        unified_category_id=leaf.id,
        confidence=100,
    )
    session.add(mapping)
    entity = Entity(
        id=uuid4(),
        source="test_source",
        source_id="unify_test_1",
        name="Test Restaurant",
        place_type="test_restaurant",
    )
    session.add(entity)
    await session.commit()

    script = get_script("unify_place_types")
    assert script is not None
    result = await script.run(
        params={"source": "test_source", "dry_run": True, "batch_size": 500},
        db=session,
    )
    assert result.success is True
    assert result.affected_count == 1
    assert "Would unify" in result.message


@pytest.mark.asyncio
async def test_unify_place_types_dry_run_unmapped(session: AsyncSession):
    entity = Entity(
        id=uuid4(),
        source="test_source",
        source_id="unmapped_1",
        name="Unmapped POI",
        place_type="unknown_type",
    )
    session.add(entity)
    await session.commit()

    script = get_script("unify_place_types")
    assert script is not None
    result = await script.run(
        params={"source": "test_source", "dry_run": True, "batch_size": 500},
        db=session,
    )
    assert result.success is True
    assert result.affected_count == 0
    assert len(result.details) > 0
    assert any(d["place_type"] == "unknown_type" for d in result.details)


@pytest.mark.asyncio
async def test_extract_attributes_dry_run(session: AsyncSession):
    entity = Entity(
        id=uuid4(),
        source="tourpedia",
        source_id="extract_test_1",
        name="Tourpedia Place",
        place_type="attraction",
        attributes={
            "tourpedia_external_links": {"facebook": "https://fb.example.com"},
            "tourpedia_photo_url": "https://example.com/photo.jpg",
        },
    )
    session.add(entity)
    await session.commit()

    script = get_script("extract_attributes")
    assert script is not None
    result = await script.run(
        params={"source": "tourpedia", "dry_run": True, "batch_size": 500},
        db=session,
    )
    assert result.success is True
    assert result.affected_count > 0
    assert any(d["step"] == "tourpedia_website" for d in result.details)
    assert any(d["step"] == "tourpedia_thumbnail" for d in result.details)


@pytest.mark.asyncio
async def test_unify_classifications_dry_run(session: AsyncSession):
    entity = Entity(
        id=uuid4(),
        source="rexby",
        source_id="classif_test_1",
        name="Rexby Place",
        place_type="restaurant",
        attributes={"rexby_secondary_categories": ["outdoor", "romantic"]},
    )
    session.add(entity)
    await session.commit()

    script = get_script("unify_classifications")
    assert script is not None
    result = await script.run(
        params={"source": "rexby", "dry_run": True, "batch_size": 500},
        db=session,
    )
    assert result.success is True
    assert result.affected_count > 0
    assert any(d["step"] == "rexby_secondary" for d in result.details)


@pytest.mark.asyncio
async def test_clean_dzt_data_dry_run(session: AsyncSession):
    entity = Entity(
        id=uuid4(),
        source="dzt",
        source_id="dzt_test_1",
        name="DZT Place",
        place_type="poi",
        country="http://onlim.com/entity/country/germany",
        region="n.v.",
    )
    session.add(entity)
    await session.commit()

    script = get_script("clean_dzt_data")
    assert script is not None
    result = await script.run(
        params={"dry_run": True, "batch_size": 1000},
        db=session,
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_all_scripts_have_dry_run():
    registry = discover_scripts()
    for name, cls in registry.items():
        instance = cls()
        param_names = {p.name for p in instance.meta.parameters}
        assert "dry_run" in param_names, f"{name} is missing dry_run parameter"
