
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Entity
from dmo.models.schemas import EntityListItem


async def search(
    session: AsyncSession,
    q: str | None = None,
    source: str | None = None,
    place_type: str | None = None,
    country: str | None = None,
    page_size: int = 20,
    cursor: str | None = None,
) -> tuple[list[EntityListItem], int, str | None, bool]:
    stmt = select(Entity).where(col(Entity.is_active))

    if source:
        stmt = stmt.where(col(Entity.source) == source)
    if place_type:
        stmt = stmt.where(col(Entity.place_type) == place_type)
    if country:
        stmt = stmt.where(col(Entity.country) == country)
    if q:
        stmt = stmt.where(
            (col(Entity.name).op("%")(q)) | (col(Entity.summary).op("%")(q))
        )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_result = await session.exec(count_stmt)
    total = count_result.one()

    if cursor:
        from dmo.services.pagination import decode_cursor
        last_id, last_name = decode_cursor(cursor)
        stmt = stmt.where(
            (col(Entity.name) > last_name) |
            ((col(Entity.name) == last_name) & (col(Entity.id) > last_id))
        )

    stmt = stmt.order_by(col(Entity.name).asc(), col(Entity.id).asc())
    stmt = stmt.limit(page_size + 1)
    result = await session.exec(stmt)
    entities = result.all()

    has_more = len(entities) > page_size
    entities = entities[:page_size]
    items = [EntityListItem.model_validate(e) for e in entities]

    next_cursor: str | None = None
    if has_more and entities:
        from dmo.services.pagination import encode_cursor
        last = entities[-1]
        next_cursor = encode_cursor(last.id, last.name)

    return items, total, next_cursor, has_more
