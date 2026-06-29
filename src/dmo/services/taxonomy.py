from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Entity, UnifiedCategory
from dmo.models.schemas import UnifiedCategoryItem

_category_level_cache: dict[str, str] | None = None


async def _build_level_cache(session: AsyncSession) -> dict[str, str]:
    """Build in-memory cache of slug → level (top/leaf) for filter auto-detection."""
    stmt = select(UnifiedCategory.slug, UnifiedCategory.parent_id).where(
        col(UnifiedCategory.is_active)
    )
    result = await session.exec(stmt)
    rows = result.all()
    return {row[0]: "top" if row[1] is None else "leaf" for row in rows}


async def get_category_level(session: AsyncSession, slug: str) -> str | None:
    """Return 'top' or 'leaf' for a given slug, or None if not found."""
    global _category_level_cache
    if _category_level_cache is None:
        _category_level_cache = await _build_level_cache(session)
    return _category_level_cache.get(slug)


async def list_categories(
    session: AsyncSession,
) -> list[UnifiedCategoryItem]:
    """Fetch active unified categories as a hierarchical tree with entity counts.

    Returns only top-level categories with nested children arrays.
    Each node includes count of active entities mapped to it.
    """
    top_stmt = (
        select(UnifiedCategory)
        .where(col(UnifiedCategory.parent_id).is_(None), col(UnifiedCategory.is_active))
        .order_by(col(UnifiedCategory.sort_order))
    )
    top_result = await session.exec(top_stmt)
    top_categories = top_result.all()

    child_ids = [c.id for c in top_categories]
    child_stmt = (
        select(UnifiedCategory)
        .where(
            col(UnifiedCategory.parent_id).in_(child_ids),
            col(UnifiedCategory.is_active),
        )
        .order_by(col(UnifiedCategory.sort_order))
    )
    child_result = await session.exec(child_stmt)
    all_children = child_result.all()

    children_by_parent: dict[int, list[UnifiedCategory]] = {}
    for child in all_children:
        if child.parent_id is not None:
            children_by_parent.setdefault(child.parent_id, []).append(child)

    all_ids = [c.id for c in top_categories] + [c.id for c in all_children]

    top_count_stmt = select(Entity.unified_category_id).where(
        col(Entity.is_active),
        col(Entity.unified_category_id).in_(child_ids),
    )
    top_count_result = await session.exec(top_count_stmt)
    top_counts: dict[int, int] = {}
    for row in top_count_result.all():
        top_counts[row[0]] = top_counts.get(row[0], 0) + 1

    leaf_count_stmt = select(Entity.unified_category_id).where(
        col(Entity.is_active),
        col(Entity.unified_category_id).in_(all_ids),
    )
    leaf_count_result = await session.exec(leaf_count_stmt)
    leaf_counts: dict[int, int] = {}
    for row in leaf_count_result.all():
        leaf_counts[row[0]] = leaf_counts.get(row[0], 0) + 1

    result = []
    for top in top_categories:
        children = []
        for c in children_by_parent.get(top.id, []):
            cid = c.id if c.id is not None else 0
            child_dict = c.model_dump(mode="json")
            child_dict["count"] = leaf_counts.get(cid, 0)
            child_dict["children"] = []
            children.append(UnifiedCategoryItem.model_validate(child_dict))
        top_count = sum(c.count for c in children)
        top_dict = top.model_dump(mode="json")
        top_dict["count"] = top_count
        top_dict["children"] = [c.model_dump(mode="json") for c in children]
        result.append(UnifiedCategoryItem.model_validate(top_dict))
    return result
