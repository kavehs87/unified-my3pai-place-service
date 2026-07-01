from sqlmodel import text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Entity
from dmo.models.schemas import EntityListItem


async def search(
    session: AsyncSession,
    q: str | None = None,
    source: str | None = None,
    place_type: str | None = None,
    unified_category: str | None = None,
    country: str | None = None,
    page_size: int = 20,
    cursor: str | None = None,
    fulltext: bool = False,
) -> tuple[list[EntityListItem], int, str | None, bool]:
    """Full-text search with filters and cursor pagination.

    Uses pg_trgm for text matching on name field by default.
    When fulltext=True, also searches summary field (slower on cold cache).
    Auto-detects unified_category level (top/leaf) for filtering.
    Returns (items, total, next_cursor, has_more).
    """
    where_parts = ["entities.is_active = true"]
    params: dict[str, object] = {}

    if source:
        where_parts.append("entities.source = :src")
        params["src"] = source
    if place_type:
        where_parts.append("entities.place_type = :ptype")
        params["ptype"] = place_type
    if country:
        where_parts.append("entities.country = :country")
        params["country"] = country
    if unified_category:
        from dmo.services.taxonomy import get_category_level

        level = await get_category_level(session, unified_category)
        if level == "top":
            where_parts.append("entities.unified_category = :ucat")
            params["ucat"] = unified_category
        elif level == "leaf":
            where_parts.append("entities.unified_subcategory = :uscat")
            params["uscat"] = unified_category
    if q:
        if fulltext:
            where_parts.append("(entities.name % :query OR entities.summary % :query)")
        else:
            where_parts.append("entities.name % :query")
        params["query"] = q

    cursor_filter = ""
    if cursor:
        from dmo.services.pagination import decode_cursor

        last_id, last_name = decode_cursor(cursor)
        cursor_filter = " AND (entities.name > :cursor_name OR (entities.name = :cursor_name AND entities.id > :cursor_id))"
        params["cursor_name"] = last_name
        params["cursor_id"] = last_id

    where_clause = " AND ".join(where_parts)
    fetch_size = page_size + 1

    rows_sql = text(f"""
        SELECT entities.*,
               COUNT(*) OVER() AS total
        FROM entities
        WHERE {where_clause}
        {cursor_filter}
        ORDER BY entities.name ASC, entities.id ASC
        LIMIT :limit
    """)
    rows_params: dict[str, object] = {"limit": fetch_size}
    rows_params.update(params)
    rows_sql = rows_sql.bindparams(**rows_params)
    rows_result = await session.exec(rows_sql)
    rows = list(rows_result.mappings().all())

    if not rows:
        return [], 0, None, False

    total = rows[0]["total"]
    has_more = len(rows) > page_size
    rows = rows[:page_size]

    items = []
    for row in rows:
        mapping = {k: v for k, v in row.items() if k not in ("total", "location")}
        entity = Entity.model_validate(mapping)
        items.append(EntityListItem.model_validate(entity))

    next_cursor: str | None = None
    if has_more and items:
        from dmo.services.pagination import encode_cursor

        last = items[-1]
        next_cursor = encode_cursor(last.id, last.name)

    return items, total, next_cursor, has_more
