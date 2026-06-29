import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Entity, PlaceTypeMapping, UnifiedCategory


@pytest.mark.asyncio
async def test_unified_categories_exist(session: AsyncSession):
    result = await session.exec(select(UnifiedCategory))
    categories = result.all()
    assert len(categories) >= 9
    slugs = {c.slug for c in categories}
    assert "food_drink" in slugs
    assert "accommodation" in slugs
    assert "attraction" in slugs
    assert "transportation" in slugs
    assert "culture" in slugs


@pytest.mark.asyncio
async def test_unified_categories_hierarchy(session: AsyncSession):
    top_level = await session.exec(
        select(UnifiedCategory).where(UnifiedCategory.parent_id.is_(None))
    )
    sub_level = await session.exec(
        select(UnifiedCategory).where(UnifiedCategory.parent_id.isnot(None))
    )
    assert len(top_level.all()) >= 9
    assert len(sub_level.all()) >= 40
    for cat in sub_level.all():
        assert cat.parent_id is not None


@pytest.mark.asyncio
async def test_place_type_mappings_exist(session: AsyncSession):
    result = await session.exec(select(PlaceTypeMapping))
    mappings = result.all()
    assert len(mappings) >= 50
    sources = {m.source for m in mappings}
    assert "osm" in sources
    assert "tourpedia" in sources


@pytest.mark.asyncio
async def test_place_type_mapping_uniqueness(session: AsyncSession):
    result = await session.exec(
        select(PlaceTypeMapping.source, PlaceTypeMapping.source_place_type)
        .group_by(PlaceTypeMapping.source, PlaceTypeMapping.source_place_type)
        .having(True)
    )
    rows = result.all()
    assert len(rows) == len(set((r.source, r.source_place_type) for r in rows))


@pytest.mark.asyncio
async def test_entity_unified_fields(session: AsyncSession):
    entity = Entity(
        source="test",
        source_id="unified-test",
        name="Unified Test Entity",
        place_type="restaurant",
        unified_category="food_drink",
        unified_subcategory="restaurant",
    )
    session.add(entity)
    await session.commit()
    await session.refresh(entity)

    result = await session.exec(select(Entity).where(Entity.id == entity.id))
    fetched = result.first()
    assert fetched.unified_category == "food_drink"
    assert fetched.unified_subcategory == "restaurant"


@pytest.mark.asyncio
async def test_entity_unified_category_id_fk(session: AsyncSession):
    entity = Entity(
        source="test",
        source_id="unified-fk-test",
        name="FK Test Entity",
        place_type="restaurant",
        unified_category_id=1,
        unified_category="food_drink",
        unified_subcategory="restaurant",
    )
    session.add(entity)
    await session.commit()
    await session.refresh(entity)
    assert entity.unified_category_id == 1


@pytest.mark.asyncio
async def test_entity_without_unified_fields(session: AsyncSession):
    entity = Entity(
        source="test",
        source_id="no-unified-test",
        name="No Unified Entity",
        place_type="restaurant",
    )
    session.add(entity)
    await session.commit()
    await session.refresh(entity)

    assert entity.unified_category is None
    assert entity.unified_subcategory is None
    assert entity.unified_category_id is None
