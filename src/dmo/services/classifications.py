import json

from sqlmodel import col, func, select
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
    stmt = select(Classification).where(col(Classification.is_active))

    if entity_id:
        stmt = stmt.where(col(Classification.entity_id) == entity_id)
    if category:
        stmt = stmt.where(col(Classification.category) == category)
    if value_code:
        stmt = stmt.where(col(Classification.value_code) == value_code)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_result = await session.exec(count_stmt)
    total = count_result.one()

    stmt = stmt.order_by(
        col(Classification.category).asc(),
        col(Classification.value_code).asc(),
        col(Classification.id).asc(),
    )

    if cursor:
        from dmo.services.pagination import decode_cursor
        last_id, sort_key = decode_cursor(cursor)
        sort_dict = json.loads(str(sort_key))
        cat = sort_dict["c"]
        val = sort_dict["v"]
        stmt = stmt.where(
            (col(Classification.category) > cat) |
            ((col(Classification.category) == cat) & (col(Classification.value_code) > val)) |
            ((col(Classification.category) == cat) &
             (col(Classification.value_code) == val) &
             (col(Classification.id) > last_id))
        )

    stmt = stmt.limit(page_size + 1)
    result = await session.exec(stmt)
    classifications = result.all()

    has_more = len(classifications) > page_size
    classifications = classifications[:page_size]

    if not classifications:
        return [], total, None, False

    entity_ids = [c.entity_id for c in classifications]
    entities_stmt = select(Entity).where(col(Entity.id).in_(entity_ids))
    entities_result = await session.exec(entities_stmt)
    entities = {e.id: e for e in entities_result.all()}

    items = []
    for c in classifications:
        entity = entities.get(c.entity_id)
        entity_ref = None
        if entity:
            entity_ref = ClassificationEntityRef.model_validate(entity)
        item = ClassificationListItem(
            id=c.id,
            entity_id=c.entity_id,
            category=c.category,
            value_code=c.value_code,
            value_title=c.value_title,
            entity=entity_ref,
        )
        items.append(item)

    next_cursor: str | None = None
    if has_more and classifications:
        from dmo.services.pagination import encode_cursor
        last = classifications[-1]
        next_cursor = encode_cursor(last.id, json.dumps({"c": last.category, "v": last.value_code}))

    return items, total, next_cursor, has_more


async def list_categories(session: AsyncSession) -> list[str]:
    stmt = (
        select(col(Classification.category))
        .where(col(Classification.is_active))
        .distinct()
        .order_by(col(Classification.category))
    )
    result = await session.exec(stmt)
    return [row for row in result.all()]
