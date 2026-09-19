import json

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Entity, UnifiedCategory
from dmo.models.schemas import UnifiedCategoryItem
from dmo.services import cache as cache_module

_LEVELS_TTL = 60


async def _build_level_cache(session: AsyncSession) -> dict[str, str]:
    """Build slug → level (top/leaf) map for filter auto-detection."""
    stmt = select(UnifiedCategory.slug, UnifiedCategory.parent_id).where(
        col(UnifiedCategory.is_active)
    )
    result = await session.exec(stmt)
    rows = result.all()
    return {row[0]: "top" if row[1] is None else "leaf" for row in rows}


async def _get_levels(session: AsyncSession) -> dict[str, str]:
    """Read the level map from the shared cache, rebuilding on miss.

    Redis-backed (60s TTL) so every worker sees taxonomy edits; the cache key
    is deleted by ``invalidate_taxonomy_cache`` on taxonomy mutations.
    """

    async def _fetch() -> str:
        return json.dumps(await _build_level_cache(session))

    cached, _ = await cache_module.cache_get_or_set(
        "taxonomy_levels", {}, fetch_fn=_fetch, ttl=_LEVELS_TTL
    )
    if cached:
        return json.loads(cached)
    return await _build_level_cache(session)


async def get_category_level(session: AsyncSession, slug: str) -> str | None:
    """Return 'top' or 'leaf' for a given slug, or None if not found."""
    levels = await _get_levels(session)
    return levels.get(slug)


async def invalidate_taxonomy_cache() -> None:
    """Clear taxonomy-derived caches after a taxonomy mutation.

    Covers the taxonomy tree, the slug→level map, and every list surface that
    filters on ``unified_category`` (re-parenting a slug changes which column
    existing queries match).
    """
    for pattern in (
        "dmo:unified_categories:*",
        "dmo:taxonomy_levels:*",
        "dmo:search:*",
        "dmo:nearby:*",
        "dmo:map:*",
        "dmo:classifications:*",
        "dmo:categories:*",
    ):
        await cache_module.cache_delete_pattern(pattern)


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
    for cat_id in top_count_result.all():
        if cat_id is not None:
            top_counts[cat_id] = top_counts.get(cat_id, 0) + 1

    leaf_count_stmt = select(Entity.unified_category_id).where(
        col(Entity.is_active),
        col(Entity.unified_category_id).in_(all_ids),
    )
    leaf_count_result = await session.exec(leaf_count_stmt)
    leaf_counts: dict[int, int] = {}
    for cat_id in leaf_count_result.all():
        if cat_id is not None:
            leaf_counts[cat_id] = leaf_counts.get(cat_id, 0) + 1

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
