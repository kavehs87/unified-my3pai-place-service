import asyncio

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Classification, Entity, Media
from dmo.models.schemas import ClassificationItem, EntityDetail, MediaItem


async def get_detail(
    session: AsyncSession,
    source: str,
    source_id: str,
) -> EntityDetail | None:
    entity_stmt = select(Entity).where(
        col(Entity.source) == source,
        col(Entity.source_id) == source_id,
        col(Entity.is_active),
    )

    entity_result, media_result, classif_result = await asyncio.gather(
        session.exec(entity_stmt),
        asyncio.ensure_future(_fetch_media_by_source(session, source, source_id)),
        asyncio.ensure_future(_fetch_classifications_by_source(session, source, source_id)),
    )
    entity = entity_result.first()

    if not entity:
        return None

    detail = EntityDetail.model_validate(entity)
    detail.media = [MediaItem.model_validate(m) for m in media_result]
    detail.classifications = [ClassificationItem.model_validate(c) for c in classif_result]
    return detail


async def _fetch_media_by_source(
    session: AsyncSession,
    source: str,
    source_id: str,
) -> list[Media]:
    stmt = (
        select(Media)
        .join(Entity, Media.entity_id == Entity.id)
        .where(
            col(Entity.source) == source,
            col(Entity.source_id) == source_id,
        )
        .order_by(col(Media.sort_order))
    )
    result = await session.exec(stmt)
    return result.all()


async def _fetch_classifications_by_source(
    session: AsyncSession,
    source: str,
    source_id: str,
) -> list[Classification]:
    stmt = (
        select(Classification)
        .join(Entity, Classification.entity_id == Entity.id)
        .where(
            col(Entity.source) == source,
            col(Entity.source_id) == source_id,
        )
    )
    result = await session.exec(stmt)
    return result.all()
