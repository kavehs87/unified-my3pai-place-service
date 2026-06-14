import json

from sqlmodel import col, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Classification, Entity
from dmo.models.schemas import ClassificationEntityRef, ClassificationListItem


async def list_classifications(
    session: AsyncSession,
    entity_id: str | None = None,
    category: str | None = None,
    value_code: str | None = None,
    page_size: int = 20,
    cursor: str | None = None,
) -> tuple[list[ClassificationListItem], int, str | None, bool]:
    """List classifications with optional filters and cursor pagination.

    Filters by entity_id, category, or value_code. Joins with entities
    to include entity reference in results. Uses COUNT(*) OVER() for single-pass total.
    Returns (items, total, next_cursor, has_more).
    """
    where_parts = ["classifications.is_active = true"]
    params: dict[str, object] = {}

    if entity_id:
        where_parts.append("classifications.entity_id::text = :entity_id")
        params["entity_id"] = entity_id
    if category:
        where_parts.append("classifications.category = :category")
        params["category"] = category
    if value_code:
        where_parts.append("classifications.value_code = :value_code")
        params["value_code"] = value_code

    cursor_filter = ""
    if cursor:
        from dmo.exceptions import AppError
        from dmo.services.pagination import decode_cursor

        last_id, sort_key = decode_cursor(cursor)
        try:
            sort_dict = json.loads(str(sort_key))
            cat = sort_dict["c"]
            val = sort_dict["v"]
        except (json.JSONDecodeError, KeyError, TypeError):
            raise AppError("Invalid cursor format", "InvalidCursor", 400)
        cursor_filter = (
            " AND ((classifications.category > :cursor_cat) OR "
            "      (classifications.category = :cursor_cat AND classifications.value_code > :cursor_val) OR "
            "      (classifications.category = :cursor_cat AND classifications.value_code = :cursor_val AND classifications.id > :cursor_id))"
        )
        params["cursor_cat"] = cat
        params["cursor_val"] = val
        params["cursor_id"] = last_id

    where_clause = " AND ".join(where_parts)
    fetch_size = page_size + 1

    rows_sql = text(f"""
        SELECT classifications.*,
               COUNT(*) OVER() AS total
        FROM classifications
        WHERE {where_clause}
        {cursor_filter}
        ORDER BY classifications.category ASC, classifications.value_code ASC, classifications.id ASC
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

    entity_ids = [r["entity_id"] for r in rows]
    entities_stmt = select(Entity).where(col(Entity.id).in_(entity_ids))
    entities_result = await session.exec(entities_stmt)
    entities = {e.id: e for e in entities_result.all()}

    items = []
    for row in rows:
        entity = entities.get(row["entity_id"])
        entity_ref = None
        if entity:
            entity_ref = ClassificationEntityRef.model_validate(entity)
        item = ClassificationListItem(
            id=row["id"],
            entity_id=row["entity_id"],
            category=row["category"],
            value_code=row["value_code"],
            value_title=row["value_title"],
            entity=entity_ref,
        )
        items.append(item)

    next_cursor: str | None = None
    if has_more and items:
        from dmo.services.pagination import encode_cursor

        last = rows[-1]
        next_cursor = encode_cursor(
            last["id"], json.dumps({"c": last["category"], "v": last["value_code"]})
        )

    return items, total, next_cursor, has_more


async def list_categories(session: AsyncSession) -> list[str]:
    """Return distinct, sorted list of active classification categories."""
    stmt = (
        select(col(Classification.category))
        .where(col(Classification.is_active))
        .distinct()
        .order_by(col(Classification.category))
    )
    result = await session.exec(stmt)
    return [row for row in result.all()]
